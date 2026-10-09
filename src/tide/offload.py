"""tide.offload — по-ходовая выгрузка сессии (``tide offload``) + Stop-хук-пинок.

Боль человека (2026-07-05): «одна перегруженная сессия, потом тяжёлая выгрузка,
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
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import fields, paths, placeholders, resolve, slug
from .arc.stream import StreamError

CONTEXT_SECTION = "## context"
CURSOR_SECTION = "## cursor — resume here"
NEXT_SECTION = "## next"
SUMMARY_SECTION = "## summary"
OFFLOADED_FIELD = "offloaded-at"
ETA_FIELD = "eta"
DELIVERABLE_FIELD = "deliverable"
DELIVERABLE_MAX = 60
_CLEAR_WORDS = ("-", "none")
CLAUDE_SESSION_FIELD = "claude-session"

# The nudge window: workspace moved but the passport untouched for this long →
# the stop is blocked once with the exact offload command. Short enough to keep
# the cursor honest, long enough to never nag mid-flow.
NUDGE_WINDOW_SECONDS = 15 * 60


class OffloadError(StreamError):
    """A user-facing offload error (no such session, nothing to write)."""


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


# --- session resolution ------------------------------------------------------

def _session_dirs(root: Path) -> List[Path]:
    """Every OPEN nested session/run dir — delegated to :mod:`tide.resolve`."""
    return resolve.open_session_dirs(root)


def find_session(root: Path, ref: str) -> Optional[Path]:
    """Resolve the open nested session *ref* names, or None if absent.

    THE resolver lives in :mod:`tide.resolve` (one matcher for every surface);
    this thin alias only translates :class:`resolve.AmbiguousRefError` into the
    user-facing :class:`OffloadError` (cand 85: ambiguity RAISES — silently
    picking a match wrote a pulse into a stranger's passport in the wild).
    """
    try:
        return resolve.find_session(root, ref)
    except resolve.AmbiguousRefError as exc:
        raise OffloadError("offload: {0}".format(exc)) from exc


def find_session_by_claude_id(root: Path, session_id: str) -> Optional[Path]:
    """The session whose passport pins ``claude-session: <session_id>``.

    Open sessions first; CLOSED ones are the fallback (cand 109): арку закрыли,
    а её чат продолжает работать — пульсу и SessionEnd-штампу есть куда лечь,
    доска не слепнет. Пульс в закрытую легален — это её собственный чат.
    """
    if not session_id:
        return None
    for entry in _session_dirs(root):
        pin = (fields.read_field(entry / "arc.md", CLAUDE_SESSION_FIELD) or "").strip()
        if pin == session_id:
            return entry
    for entry in resolve.closed_session_dirs(root):
        pin = (fields.read_field(entry / "arc.md", CLAUDE_SESSION_FIELD) or "").strip()
        if pin == session_id:
            return entry
    return None


# --- eta / deliverable --------------------------------------------------------

def parse_eta(raw: str, now: Optional[datetime] = None) -> str:
    """Normalize an ``--eta`` value to ISO with the local offset; ``""`` = clear.

    Accepts ``HH:MM`` (today, local — an already-past time stays today: it is
    then overdue, never rolled to tomorrow), ``DD.MM HH:MM`` (this year) or a
    full ISO stamp (naive → local offset). ``-``/``none`` clears the field.
    """
    value = " ".join((raw or "").split())
    if value.lower() in _CLEAR_WORDS:
        return ""
    base = (now or datetime.now()).astimezone()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    m2 = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.? (\d{1,2}):(\d{2})", value)
    try:
        if m:
            dt = base.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                              second=0, microsecond=0)
        elif m2:
            dt = datetime(base.year, int(m2.group(2)), int(m2.group(1)),
                          int(m2.group(3)), int(m2.group(4))).astimezone()
        else:
            dt = datetime.fromisoformat(value)
            dt = dt.astimezone() if dt.tzinfo is None else dt
    except ValueError:
        raise OffloadError(
            "offload: --eta {0!r} not understood — use HH:MM, 'DD.MM HH:MM', "
            "ISO, or '-' to clear".format(raw)
        ) from None
    return dt.isoformat(timespec="seconds")


def clean_deliverable(raw: str) -> str:
    """One line, ≤ ~60 chars (cut with «…»); ``-``/``none`` → ``""`` = clear."""
    value = " ".join((raw or "").split())
    if value.lower() in _CLEAR_WORDS:
        return ""
    if len(value) > DELIVERABLE_MAX:
        value = value[:DELIVERABLE_MAX - 1].rstrip() + "…"
    return value


# --- the offload write -------------------------------------------------------

def offload(root: Path, ref: str, *, note: str = "", cursor: str = "",
            next_steps: str = "", summary: str = "",
            eta: Optional[str] = None, deliverable: Optional[str] = None) -> Path:
    """Append *note* to ``## context``; optionally reset cursor, ``## next``, summary.

    ФОРМА ЗАПИСИ (закон доски, 07.07 — «агенты пишут так, чтобы можно
    было анализировать»): cursor = ТЕКУЩЕЕ ДЕЙСТВИЕ одной строкой, настоящее
    время («женю доску с формой записи»); next = 1–3 следующих шага через « · »;
    note = что решил/сделал, по-человечески, без тех-жаргона в первых словах.
    Доска показывает их буквально: cursor → «сейчас», next → «дальше».

    *summary* — ЧТО В ЭТОЙ АРКЕ ЛЕЖИТ, одной строкой, для того, кто выбирает,
    в какую арку войти. До этого ``## summary`` заполнялся только на хендоффе,
    поэтому у живых сессий он стоял шаблонной болванкой, и оркестратор не мог
    отличить арку с тремя отчётами дня от пустой. Строка живёт здесь, а не в
    ``## context``: context — журнал (растёт), summary — витрина (переписывается).

    *eta* / *deliverable* — КОГДА сессия сдаст и ЧТО человек получит: поля
    паспорта ``eta:`` (ISO со смещением) и ``deliverable:``, переписываются,
    не копятся. ``None`` — не трогать, ``-``/``none`` — стереть.
    """
    if eta is not None:
        eta = parse_eta(eta)
    if deliverable is not None:
        deliverable = clean_deliverable(deliverable)
    if eta is None and deliverable is None and not any(
            s.strip() for s in (note or "", cursor or "", next_steps or "", summary or "")):
        raise OffloadError(
            "offload: nothing to write — pass a note and/or --cursor/--next/--summary/--eta/--deliverable "
            "(правило: одна строка «где стою / что решил / что дальше»)"
        )
    entry = find_session(Path(root), ref)
    if entry is None:
        names = ", ".join(e.name for e in _session_dirs(Path(root))) or "(none)"
        raise OffloadError(
            "offload: no open session matching {0!r}. Open sessions: {1}".format(ref, names)
        )
    return write_pulse(entry, note=note, cursor=cursor, next_steps=next_steps,
                       summary=summary, eta=eta, deliverable=deliverable)


def write_pulse(entry: Path, *, note: str = "", cursor: str = "",
                next_steps: str = "", summary: str = "",
                now: Optional[datetime] = None,
                eta: Optional[str] = None, deliverable: Optional[str] = None) -> Path:
    """Write a pulse to a KNOWN session dir — the resolution-free core of :func:`offload`.

    Callers that already hold the exact session dir MUST use this, not ``offload(root,
    ref)``: resolving by slug (:func:`find_session`) collides when sessions share a slug
    (every handoff pickup is ``NN-pickup`` → entry_slug ``pickup``), so a slug lookup
    lands the pulse on the FIRST same-slug session. That silently mis-stamped a fresh
    pickup's "нить принята" onto an OLDER sibling — the reception seam looked half-open
    on the real session (caught by a live 6-hop dogfood, cand 78). Taking the exact dir
    removes the ambiguity entirely.

    *now* фиксирует метку времени (:mod:`tide.acts` пишет акты через этот же
    вход и должен уметь ставить своё время в тестах); по умолчанию — сейчас.
    """
    passport = entry / "arc.md"
    text = passport.read_text(encoding="utf-8")

    # A pulse with only --cursor/--next is still a pulse: without a line in
    # ``## context`` the thread screen counts it as nothing («0 pulses in
    # ## context») and the session looks idle while it works. So the cursor (or,
    # lacking it, the next step) lands in the journal as a short line of its own.
    if not (note or "").strip():
        note = _cursor_only_note(cursor, next_steps)

    if (note or "").strip():
        line = "- {0} — {1}".format(_now_iso(now), " ".join(note.split()))
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
    if (summary or "").strip():
        _replace_section(passport, SUMMARY_SECTION, " ".join(summary.split()))
    for key, value in ((ETA_FIELD, eta), (DELIVERABLE_FIELD, deliverable)):
        if value is None:
            continue
        if value:
            fields.set_field(passport, key, value)
        else:
            fields.remove_field(passport, key)
    fields.set_field(passport, OFFLOADED_FIELD, _now_iso(now))
    return passport


def _cursor_only_note(cursor: str, next_steps: str) -> str:
    """The ``## context`` line for a pulse that carried no note — or ``""``."""
    if (cursor or "").strip():
        return "курсор → " + " ".join(cursor.split())
    if (next_steps or "").strip():
        return "дальше → " + " ".join(next_steps.split())
    return ""


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


def nudge_reason(root: Path, session_id: str, *, now: Optional[float] = None,
                 activity_m: float = 0.0) -> Optional[str]:
    """The block-reason when *session_id*'s arc needs an offload, else None.

    Triggers when WORK happened AND the passport has not been touched for
    :data:`NUDGE_WINDOW_SECONDS`. "Work happened" is the newest of two signals:
    the arc's ``workspace/`` mtime, and *activity_m* — the session TRANSCRIPT mtime
    (agent is alive). The transcript signal is what catches a **blind session**: an
    agent doing all its work in a nested/sibling code-repo never moves the arc
    workspace, so the old workspace-only check never fired and the board stayed empty
    mid-work (cand 87). Deterministic file mtimes — no git, no LLM, milliseconds.
    """
    entry = find_session_by_claude_id(Path(root), session_id)
    if entry is None:
        return None
    passport = entry / "arc.md"
    try:
        passport_m = passport.stat().st_mtime
    except OSError:
        return None
    work_m = max(_newest_mtime(entry / "workspace"), activity_m or 0.0)
    if work_m <= passport_m:
        return None  # passport is as fresh as the work — nothing owed
    now_ts = now if now is not None else datetime.now().timestamp()
    if now_ts - passport_m < NUDGE_WINDOW_SECONDS:
        return None  # touched recently — don't nag mid-flow
    # Thread-qualify the suggested ref: a bare session slug ('session', 'pickup')
    # collides across threads and now RAISES (cand 85) — the nudge must not hand the
    # agent a command that fails. '<thread>/<session>' always resolves.
    ref = "{0}/{1}".format(slug.entry_slug(entry.parent.parent.name), slug.entry_slug(entry.name))
    msg = (
        "tide: выгрузка отстала — ты работаешь по нити {0}, а её паспорт не "
        "трогали дольше {1} мин (доска слепа). Сделай сейчас, это 10 секунд:\n"
        "  tide offload {2} --cursor \"<текущее действие, наст. время>\" --next \"<шаги через · >\" \"<что сделал>\"\n"
        "Правило: одна строка на каждое, без отчётов. Потом заканчивай ход."
    ).format(entry.name, NUDGE_WINDOW_SECONDS // 60, ref)
    return msg + _blind_goal_suffix(entry)


def _blind_goal_suffix(session_entry: Path) -> str:
    """A one-line START-GATE add-on when the thread's board goal is still blind (cand 81/87).

    The offload nudge already fired because the board is blind; if the *reason* it
    reads empty is also a slug/placeholder goal (``goal: handoff`` on ``01-@handoff``),
    tell the agent to set real words in the same breath — otherwise it fixes the
    offload and the nit still shows no purpose. Best-effort: any error ⇒ no suffix.
    """
    try:
        thread = session_entry.parent.parent  # session → arcs/ → thread
        goal_doc = thread / "{0}-goal.md".format(slug.entry_slug(thread.name))
        raw = (fields.read_field(goal_doc, "goal") or "").strip() if goal_doc.is_file() else ""
        if placeholders.is_blind_goal(raw, slug.entry_slug(thread.name)):
            return (
                "\ntide: и у нити слепая цель («{0}») — задай живую: "
                "tide arc set-goal {1} \"<цель одной строкой>\"."
            ).format(raw or "—", slug.entry_slug(thread.name))
    except Exception:  # noqa: BLE001  a nudge add-on must never break the hook
        return ""
    return ""


# --- CLI + hook wiring -------------------------------------------------------

# A pulse CLAIMS the nit is finished only when a closure verb attaches to a CLOSABLE
# OBJECT (нить/арка/тред/PR/дельта/ветка) — a bare «закрыт» also describes gates and
# steps («старт-гейт закрыт») and a substring match trained agents to avoid honest
# words in pulses (cand 106). Both word orders, up to two words in between; verbs are
# perfective only (intent like «закрою нить позже» must not warn). The link may NOT
# cross a clause boundary (,.;:—…): «старт-гейт закрыт, строю план НИТИ» — «нити»
# из другой клаузы, это не заявление о закрытии (второй ложняк cand 106, 16.07).
_CLOSABLE = r"(?:нит\w+|арк\w+|тред\w+|thread|ветк\w+|branch|pr|пиар\w*|делт\w+|delta)"
_CLOSED_VERB = r"(?:закрыт\w*|закрыл\w*|влит\w*|влил\w*|смерж\w*|выпущен\w*|merged|shipped|closed)"
_LINK = r"(?:{s}(?:\w+{s}){{0,2}})".format(s=r"[ \t]+")  # чистые пробелы: любой знак рвёт связку
_CLOSURE_CLAIM = re.compile(
    r"{o}{l}{v}|{v}{l}{o}".format(o=_CLOSABLE, v=_CLOSED_VERB, l=_LINK),
    re.IGNORECASE,
)


def _closure_word_warning(passport: Path, blob: str) -> Optional[str]:
    """Warn when the pulse SAYS closed but its thread is OPEN on disk (cand 80).

    The pulse lands in ``<thread>/arcs/<session>/arc.md``; the thread is the dir two
    levels up. If it isn't wrapped ``__…__`` (closed) yet the text claims closure, the
    board will still paint the nit live — nudge the human to close it for real.
    """
    low = (blob or "").lower()
    if not _CLOSURE_CLAIM.search(low):
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


def _confirm_pending_pickup(passport: Path) -> Optional[str]:
    """Flip a handoff still RESERVED for this session's sid (belt to the hook).

    The flip normally rides the first UserPromptSubmit; when that hook could not
    fire (live 14.07: the project had no hooks wired at spawn) the offer stays
    reserved and the board paints «поднимается» forever. A pulse is the session
    proving it is alive — a stronger hello than a prompt — so it closes the same
    seam through the same single flip point (I4). Silent no-op for ordinary
    sessions; never breaks an offload.

    Two ways in, because the passport is unstamped until reception (cand 140):
    by the CALLER's sid when an offer is reserved for it, else by the session's own
    directory — the offer's seed lives inside it, so the reservation is found
    without a sid. Either way only the RECEIVING session confirms (SOC-101 F26):
    the caller is ``$CLAUDE_CODE_SESSION_ID`` (+ its AO mapping), never the
    passport it writes into — the old session pulsing into its successor's arc
    confirmed the reception for it.
    """
    try:
        from . import handoff_queue
        home = paths.control_home()
        callers = handoff_queue.caller_sids(home)
        claimed = None
        for sid in callers:
            claimed = handoff_queue.confirm_for_session(home, sid, as_session=callers[0])
            if claimed:
                break
        if claimed is None:
            claimed = handoff_queue.confirm_for_session_dir(
                home, Path(passport).parent, callers=callers)
        return str(claimed["name"]) if claimed else None
    except Exception:  # noqa: BLE001 — the fallback must never break a pulse
        return None


def _note_first_pulse(passport: Path, *, note: str, cursor: str):
    """The receiver's first real pulse → its handoff offer (SOC-101 п.2: what the
    accepting session understood, for ``tide handoffs audit``). → the offer record
    when THIS pulse stamped the first one, else None. Never breaks a pulse."""
    try:
        from . import handoff_queue
        return handoff_queue.note_first_pulse(paths.control_home(), Path(passport).parent,
                                              note=note, cursor=cursor)
    except Exception:  # noqa: BLE001
        return None


def _retell_warning(rec, passport: Path, blob: str) -> Optional[str]:
    """Старт-гейт приёма (SOC-101 F35): первый пульс принявшей без пересказа §4.2 —
    подсказка со списком недостающего. Не блок: пульс уже записан."""
    if not rec:
        return None
    try:
        from . import handoff_queue
        said = " ".join((blob, str(rec.get("understood_goal") or ""),
                         str(rec.get("first_pulse") or "")))
        gaps = handoff_queue.retell_gaps(said)
        if not gaps:
            return None
        entry = Path(passport).parent
        ref = "{0}/{1}".format(slug.entry_slug(entry.parent.parent.name),
                               slug.entry_slug(entry.name))
        return handoff_queue.retell_hint(gaps, ref)
    except Exception:  # noqa: BLE001 — подсказка не смеет уронить пульс
        return None


def _pass_map_warning(note: str) -> None:
    """Пульс «ПЕРЕДАЧА» без метки «Карта:» (SOC-101 C11) — предупредить передающую
    сессию сразу, в stderr, а не только на сборке сида. Метка — начало ноты (F24),
    распознавание и текст — те же, что у ``seed_draft.warn_missing_map`` (оба из
    :mod:`tide.handoff_contract`: offload — домен и сборщик-лаунчер звать не может).
    Не блок: пульс уже записан, rc не меняется."""
    try:
        from . import handoff_contract
        if handoff_contract.pass_note_without_map(" ".join((note or "").split())):
            print("tide: внимание — {0}".format(handoff_contract.MAP_WARNING),
                  file=sys.stderr)
    except Exception:  # noqa: BLE001 — предупреждение не смеет уронить пульс
        return


# SOC-101 C1: в пульсе ПЕРЕДАЧА — метка «Ожидание Гриши:» (или «Ожидание:») и
# «Хвосты:» («Открыто:»). F62: метки ищет тот же разборщик, что сборщик сида
# (:func:`tide.handoff_contract._label_re`) — начало ноты, конец фразы, тире или
# « · »; свой поиск не видел метку после « · » в длинном пульсе и подсказывал зря.
def _pass_label_keys(text: str) -> set:
    """Ключи меток контракта в ноте (``expectation``, ``tails``, ``entry_map``…),
    включая метки без текста за ними (их ``pulse_labels`` отбрасывает)."""
    from . import handoff_contract
    keys = set()
    for m in handoff_contract._label_re().finditer(text):
        group = next(g for g, v in m.groupdict().items() if v is not None)
        keys.add(group.rsplit("_", 1)[0])
    return keys


EXPECT_WARNING = ("в пульсе ПЕРЕДАЧА нет метки «Ожидание Гриши:», а слой «ожидание "
                  "Гриши» нити пуст — новая сессия не узнает, чего Гриша ждёт; впиши "
                  "его словами Гриши с датой: tide offload \"ПЕРЕДАЧА. … Ожидание Гриши: "
                  "«…» (tg N, дата)\"")
TAILS_HINT = ("назови открытые хвосты меткой «Хвосты:» — что красное, что не проверено "
              "вживую, что обещано (или «Хвосты: нет»)")


def _expectation_layer(passport: Path) -> List[str]:
    """Строки слоя «### ожидание Гриши» в ``## where we are`` goal-файла нити
    (заглушки ``<…>`` не в счёт). Нить — дедушка сессии (…/NN-@thread/arcs/NN-s)."""
    from .arc.stream import passport_path

    try:
        thread = Path(passport).parent.parents[1]
        text = passport_path(thread).read_text(encoding="utf-8")
    except (OSError, IndexError):
        return []
    out: List[str] = []
    section = head = ""
    for ln in text.splitlines():
        if ln.startswith("## "):
            section, head = ln[3:].strip().lower(), ""
        elif ln.startswith("### "):
            head = ln[4:].strip().lower()
        elif (section.startswith("where we are") and head.startswith("ожидание")
              and ln.strip() and not ln.strip().startswith("<")):
            out.append(ln.rstrip())
    return out


def _pass_labels_warning(passport: Path, note: str) -> None:
    """Пульс «ПЕРЕДАЧА» (SOC-101 C1): без «Ожидание Гриши:» при пустом слое нити —
    предупреждение, как о «Карта:» (C11); без «Хвосты:» — мягкая подсказка. В stderr,
    rc не меняется, ошибка — молчание."""
    try:
        from . import handoff_contract
        text = " ".join((note or "").split())
        if not handoff_contract.is_pass_note(text):
            return
        keys = _pass_label_keys(text)
        if "expectation" not in keys and not _expectation_layer(passport):
            print("tide: внимание — {0}".format(EXPECT_WARNING), file=sys.stderr)
        if "tails" not in keys:
            print("tide: подсказка — {0}".format(TAILS_HINT), file=sys.stderr)
    except Exception:  # noqa: BLE001 — предупреждение не смеет уронить пульс
        return


def _main_gate(root: Path, ref: str):
    """Старт-гейт «главного» нити (решение Гриши № 24) — :func:`tide.arc.plan_main.gate`
    для сессии *ref*; None — молчим (или сессия не нашлась: ошибку скажет offload)."""
    try:
        entry = find_session(Path(root), ref)
        if entry is None:
            return None
        from .arc import plan_main
        return plan_main.gate(entry)
    except Exception:  # noqa: BLE001 — гейт не смеет уронить пульс
        return None


def _cmd_offload(args) -> int:
    root = paths.require_tide_root()
    note = " ".join(getattr(args, "note", []) or [])
    cursor = getattr(args, "cursor", "") or ""
    next_steps = getattr(args, "next_steps", "") or ""
    summary = getattr(args, "summary", "") or ""
    verdict = _main_gate(root, args.session)
    if verdict is not None and verdict.block:
        print(verdict.reason, file=sys.stderr)
        return 1
    passport = offload(root, args.session, note=note, cursor=cursor,
                       next_steps=next_steps, summary=summary,
                       eta=getattr(args, "eta", None),
                       deliverable=getattr(args, "deliverable", None))
    print("tide: offloaded → {0}".format(passport))
    if verdict is not None:
        print(verdict.reason, file=sys.stderr)
    confirmed = _confirm_pending_pickup(passport)
    if confirmed:
        print("tide: handoff {0} confirmed by this pulse (the first-prompt flip "
              "was still pending)".format(confirmed))
    first = _note_first_pulse(passport, note=note, cursor=cursor)
    retell = _retell_warning(first, passport, " ".join((note, cursor, next_steps)))
    if retell:
        print(retell, file=sys.stderr)
    warn = _closure_word_warning(passport, " ".join((note, cursor, next_steps)))
    if warn:
        print(warn, file=sys.stderr)
    _pass_map_warning(note)
    _pass_labels_warning(passport, note)
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
        # The transcript mtime is the 'agent is alive' signal — it catches a blind
        # session working in a nested repo, where the arc workspace never moves (cand 87).
        activity_m = 0.0
        tp = payload.get("transcript_path")
        if tp:
            try:
                activity_m = Path(tp).stat().st_mtime
            except OSError:
                activity_m = 0.0
        reason = nudge_reason(paths.require_tide_root(), str(session), activity_m=activity_m)
        if reason:
            # A DISSOLVED head (handed this thread off, offer taken) must STAND DOWN,
            # not be pushed to pulse: an offload would re-surface it on the board as
            # active — the 'Mickey 17' multiple the handoff seam exists to prevent
            # (cand 91). Never BLOCK it (a Stop-block forces it to keep going, the
            # opposite of standing down) — one stderr note instead, then let it stop.
            stand_down = _dissolved_stand_down(str(session))
            if stand_down:
                print(stand_down, file=sys.stderr)
            else:
                print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001  a hook must never raise
        print("tide: [offload-nudge] skipped: {0}".format(exc), file=sys.stderr)
    return 0


def _dissolved_stand_down(session: str) -> Optional[str]:
    """Stand-down note when *session* has DISSOLVED (offered this thread, offer taken).

    The successor holds the thread now (one orchestrator per thread), so nudging this
    head to offload is wrong — it would re-surface as active on the board (cand 91).
    Returns None when the session still holds, or when the control-home can't be
    resolved (cand 90's no-control-home case) — then the normal nudge stands.
    """
    if not session:
        return None
    # First signal: the EXPLICIT stamp (I6) — take() dissolves the origin's passport
    # mechanically, so the check is local, cheap, and survives archived offer records.
    try:
        root = paths.find_tide_root()
        arc = find_session_by_claude_id(root, session) if root else None
        if arc is not None and (fields.read_field(arc / "arc.md", "dissolved") or "").strip():
            return (
                "tide: ты растворён (dissolved: в паспорте — нить у преемника). "
                "Стой down, не пульсируй: offload вернул бы тебя на доску как активного (Микки-17)."
            )
    except Exception:  # noqa: BLE001 — a hook check must never raise
        pass
    try:
        from . import handoff_queue as hq
        rec = hq.is_dissolved(paths.control_home(), session)
    except Exception:  # noqa: BLE001 — a hook check must never raise
        return None
    if not rec:
        return None
    return (
        "tide: ты отдал нить (оффер {0} → держит {1}) — стой down, не пульсируй. "
        "Нить ведёт преемник; offload вернул бы тебя на доску как активного (Микки-17)."
    ).format(rec.get("name"), rec.get("taken_by"))


def register(subparsers) -> None:
    """Add the top-level ``offload`` command (called by cli.py)."""
    p = subparsers.add_parser(
        "offload",
        help="offload as you go: a line into the session's '## context' "
             "(+ --cursor)",
    )
    p.add_argument("session", help="open session slug (nested resolve across threads)")
    p.add_argument("--cursor", help="WHAT YOU ARE DOING right now, one line, "
                                    "present tense ('wiring the board to the form')")
    p.add_argument("--next", dest="next_steps",
                   help="1-3 next steps separated by ' · ' — the board shows "
                        "them as what's next")
    p.add_argument("--summary",
                   help="ONE line: what this arc holds, for whoever is choosing "
                        "which arc to open — rewritten, not appended")
    p.add_argument("--eta",
                   help="WHEN you will deliver: HH:MM (today; past = overdue), "
                        "'DD.MM HH:MM' or ISO — stored as ISO, rewritten; '-' clears")
    p.add_argument("--deliverable",
                   help="WHAT the human will get, one short line (<=60 chars: "
                        "'PR #171', 'screenshot of the screen') — rewritten; '-' clears")
    p.add_argument("note", nargs="*", help="context note: what you decided or "
                                           "did, in plain words")
    p.set_defaults(func=_cmd_offload, _cmd="offload")

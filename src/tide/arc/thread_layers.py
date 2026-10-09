"""tide.arc.thread_layers — три слоя нити на выходе сессии (SOC-101, acceptance-01 §12 п.1).

Слои ``## where we are`` goal-файла нити — «ожидание Гриши», «где мы», «опыт» —
главный источник сида: без них блок «ожидание Гриши» пуст при любом сборщике
(C1 приёмки). Нити, рождённые до c51b04f, каркаса слоёв не имеют вовсе.

* :func:`empty_layers` / :func:`exit_warning` — какие слои пусты; ``tide handoff``
  (и ``--auto``) предупреждает, но НЕ блокирует передачу: нить с пустыми слоями
  всё равно лучше передать, чем застрять.
* :func:`write_layers` / ``tide thread layer <нить> --expect … --where … --exp …`` —
  вписать слои командой: голове role-gate писать файлы запрещает, а CLI tide ей
  разрешён (SOC-101 F53).
* :func:`write_main` / ``tide thread layer <нить> --main "SOC-N · кому сдавать · с кем
  не дублировать" [--replace]`` — раздел «главное» в ``plan.md`` нити (его читает
  сборщик сида через :mod:`tide.arc.plan_main`; SOC-101 F61).
* :func:`seed_skeleton` — досевает пустой каркас недостающих слоёв в goal-файл
  старой нити, не трогая остальное (``tide thread layers --seed-skeleton <нить>``).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, handoff_contract as contract, io as _io, \
    name_dots as _name_dots, paths, resolve
from . import plan_main as _plan_main, stream as _stream
from .stream import StreamError

WHERE_HEAD = "## where we are"


class LayersError(StreamError):
    """Нет такой нити / не нить."""


def _where_span(lines: List[str]) -> "tuple[int, int]":
    """``(начало тела, конец)`` секции ``## where we are``; ``(-1, -1)`` — нет."""
    for i, ln in enumerate(lines):
        if ln.strip().lower() == WHERE_HEAD:
            end = next((j for j in range(i + 1, len(lines))
                        if lines[j].startswith("## ")), len(lines))
            return i + 1, end
    return -1, -1


def _layer_heads(body: List[str]) -> List[str]:
    return [ln[4:].strip().lower() for ln in body if ln.startswith("### ")]


def empty_layers(thread_dir: Path) -> List[str]:
    """Имена слоёв контракта (:data:`contract.THREAD_LAYERS`), в которых нет живых
    строк — нет подзаголовка или под ним одни заглушки ``<…>``."""
    try:
        lines = _stream.passport_path(Path(thread_dir)).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        lines = []
    start, end = _where_span(lines)
    live = set()
    head = ""
    for ln in (lines[start:end] if start >= 0 else []):
        if ln.startswith("### "):
            head = ln[4:].strip().lower()
        elif ln.strip() and not ln.strip().startswith("<") and head:
            live.add(head)
    return [contract.name(key) for key, _ph in contract.THREAD_LAYERS
            if not any(h.startswith(contract.name(key).lower()) for h in live)]


# Флаги ``tide thread layer`` → ключ слоя контракта (SOC-101 F53).
LAYER_FLAGS = (("expect", "expectation"), ("where", "where"), ("exp", "experience"))


def layer_cmd(thread: str) -> str:
    """Готовая команда, которой голова (ей role-gate запрещает писать файлы) вписывает
    слои нити: ``tide thread layer <нить> --expect "…" --where "…" --exp "…"``."""
    return ('tide thread layer {0} --expect "<чего ждёт Гриша — его словами, у каждой цитаты tg N>" '
            '--where "<сделано · идёт · ждёт Гришу>" --exp "<грабли, отвергнутое>"'
            .format(thread))


def main_cmd(thread: str) -> str:
    """Команда, которой голова вписывает «главное» нити в plan.md (SOC-101 F61)."""
    return ('tide thread layer {0} --main "<SOC-N> · <кому и каким каналом сдавать> · '
            '<с кем не дублировать>"'.format(thread))


def exit_warning(thread_dir: Path) -> Optional[str]:
    """Предупреждение на передаче, когда слои нити пусты (None — все заполнены)."""
    empty = empty_layers(thread_dir)
    if not empty:
        return None
    goal = _stream.passport_path(Path(thread_dir))
    from .. import slug as _slug
    name = _slug.entry_slug(Path(thread_dir).name)
    main = ""
    if not _plan_main.live_lines(Path(thread_dir)):
        main = ("\n«Главное» нити ({0}) тоже пусто — его читает сид первым:\n  {1}"
                .format(Path(thread_dir) / _plan_main.PLAN_FILE, main_cmd(name)))
    return ("tide: слои нити пусты — {0} ({1}, ## where we are). Новая сессия не узнает "
            "их из сида: впиши их до передачи словами Гриши — командой (файлы руками "
            "не нужны, голове она разрешена):\n  {2}{4}\nКаркас слоёв она досеет сама "
            "(только каркас — tide thread layers --seed-skeleton {3}). Передачу не "
            "останавливаю.".format(
                ", ".join("«{0}»".format(n) for n in empty), goal,
                layer_cmd(name), Path(thread_dir).name, main))


def split_main(values) -> List[str]:
    """Значения ``--main`` (флаг можно повторять) → строки «главного»: каждое ещё
    режется по « · », пробелы схлопываются, пустые и повторы уходят. Имя сессии
    «роль · NN» («main · 05», «designer · 09») не рвётся (SOC-143 F70) — тот же
    разборщик, что у сида (:func:`tide.name_dots.split_dots`)."""
    out: List[str] = []
    for v in values or []:
        for part in _name_dots.split_dots(" ".join(str(v or "").split())):
            part = part.strip().strip("·").strip()
            if part and part not in out:
                out.append(part)
    return out


def write_main(thread_dir: Path, items: List[str], *, replace: bool = False) -> List[str]:
    """Вписать строки в раздел «## главное — читать первым» ``plan.md`` нити (F61).

    Формат — тот, что читает сборщик сида (:func:`tide.arc.plan_main.live_lines`):
    заголовок с префиксом ``## главное``, под ним строки ``- текст``. Нет plan.md —
    создаётся каркасом нити (:func:`tide.arc.templates.thread_plan_md`); нет раздела —
    встаёт после заголовка ``# …`` плана. Без *replace* строки дописываются в конец
    раздела (подсказки ``<…>`` уходят, живые строки и повторы остаются как были);
    с *replace* тело раздела заменяется целиком. Вернуть вписанные строки."""
    items = split_main(items)
    if not items:
        return []
    plan = Path(thread_dir) / _plan_main.PLAN_FILE
    if not plan.is_file():
        from . import templates
        from .. import slug as _slug
        _io.atomic_write(plan, templates.thread_plan_md(
            _slug.entry_slug(Path(thread_dir).name)))
    lines = plan.read_text(encoding="utf-8").splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.startswith(_plan_main.PREFIX)), -1)
    if head < 0:
        at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("# ")), 0)
        lines = lines[:at] + [""] * bool(at) + [_plan_main.HEADING, ""] + lines[at:]
        head = at + bool(at)
    stop = next((j for j in range(head + 1, len(lines)) if lines[j].startswith("## ")),
                len(lines))
    body = [] if replace else [ln for ln in lines[head + 1:stop]
                               if not ln.strip().lstrip("-* ").startswith("<")]
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    have = {ln.strip().lstrip("-* ").strip() for ln in body}
    added = [it for it in items if replace or it not in have]
    body += ["- {0}".format(it) for it in added]
    tail = [""] if stop < len(lines) else []
    lines = lines[:head + 1] + body + tail + lines[stop:]
    _io.atomic_write(plan, "\n".join(lines).rstrip("\n") + "\n")
    return added


# F65: слои, которые ``tide thread layer`` ЗАМЕНЯЕТ целиком (прежнее — в журнал).
# «Где мы» — снимок положения: дописанный, он копил противоречия (SOC-106 In Review
# и Done, разные счёты в одном сиде). Ожидание и опыт — ленты, дописываются.
REPLACE_KEYS = ("where",)
JOURNAL_HEAD = "## журнал слоёв"


def _journal(lines: List[str], name: str, stamp: str, old: List[str]) -> List[str]:
    """Прежние строки слоя → раздел ``## журнал слоёв`` goal-файла (вне ``## where
    we are`` — сборщик сида его не читает). Свежая запись — сверху раздела."""
    entry = ["### {0} — заменено {1}".format(name, stamp)] + old + [""]
    at = next((i for i, ln in enumerate(lines)
               if ln.strip().lower() == JOURNAL_HEAD), -1)
    if at < 0:
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines + ["", JOURNAL_HEAD] + entry
    rest = lines[at + 1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return lines[:at + 1] + entry + rest


# SOC-143 F81: штамп слоя — время записи, а не время слов. Текст, что уже начат
# своей датой («09.10 (TG, через менеджера) — …»), штамп не получает: склейка
# «09.10 19:22 — 09.10 …» читалась как время слов. У ссылок «tg N» время слов
# берётся из журнала пульта (:func:`tide.arc.dialog.tg_times`). Время записи слоя
# ожидания — поле ``expect-written:`` goal-файла (его читает :func:`freshness`).
EXPECT_AT_FIELD = "expect-written"
_LEAD_DATE = re.compile(r"^[(\[]?\s*(?:\d{1,2}\.\d{1,2}(?:\.\d{2,4})?|\d{4}-\d{2}-\d{2})(?![\d.])")
_TG_REF = re.compile(r"\b(tg)[ :]*(\d{3,6})\b"
                     r"(?P<date>\s*,\s*\d{1,2}\.\d{1,2}(?:\.\d{2,4})?(?![\d.]))?"
                     r"(?P<time>\s*,?\s*~?\d{1,2}:\d{2})?", re.I)


def tg_refs(text: str) -> List[int]:
    """Номера «tg N» в *text*, у которых ещё нет времени («tg 3573, 18:50» — есть)."""
    return [int(m.group(2)) for m in _TG_REF.finditer(text or "") if not m.group("time")]


def stamp_line(text: str, now: datetime, times: Optional[Dict[int, datetime]] = None) -> str:
    """Строка слоя ``- ДД.ММ ЧЧ:ММ — текст`` (F81). Текст начат датой — без штампа.
    Иначе штамп — время первой цитаты «tg N» из *times* (журнал пульта), нет его —
    *now*. Остальные «tg N» с известным временем получают ``, ДД.ММ ЧЧ:ММ``."""
    times = times or {}
    lead = bool(_LEAD_DATE.match(text))
    first = None
    if not lead:
        first = next((times[int(m.group(2))] for m in _TG_REF.finditer(text)
                      if not m.group("time") and int(m.group(2)) in times), None)

    def _sub(m):
        at = times.get(int(m.group(2)))
        if m.group("time") or at is None or at == first:
            return m.group(0)
        return "{0} {1}, {2}".format(m.group(1), m.group(2), at.strftime("%d.%m %H:%M"))

    text = _TG_REF.sub(_sub, text)
    if lead:
        return "- " + text
    return "- {0} — {1}".format((first or now).strftime("%d.%m %H:%M"), text)


def write_layers(goal_path: Path, values: Dict[str, str], *,
                 now: Optional[datetime] = None,
                 tg_times: Optional[Dict[int, datetime]] = None) -> List[str]:
    """Вписать строки в слои ``## where we are`` goal-файла нити (SOC-101 F53).

    *values* — ``{ключ слоя контракта: текст}`` (``expectation`` / ``where`` /
    ``experience``). Каркаса нет — досевается (:func:`seed_skeleton`). Строка
    ``- ДД.ММ ЧЧ:ММ — текст`` встаёт в конец слоя, заглушка ``<…>`` слоя уходит,
    живые строки остаются — кроме «где мы» (:data:`REPLACE_KEYS`, F65): его тело
    заменяется одной новой строкой, прежние строки уходят в ``## журнал слоёв``
    с датой замены. Ровно те секции, что читают :func:`empty_layers` и
    сборщик сида. Вернуть имена слоёв, куда вписано."""
    goal_path = Path(goal_path)
    values = {k: " ".join(str(v or "").split()) for k, v in values.items()}
    values = {k: v for k, v in values.items() if v}
    if not values:
        return []
    seed_skeleton(goal_path)
    lines = goal_path.read_text(encoding="utf-8").splitlines()
    now = now or datetime.now()
    stamp = now.strftime("%d.%m %H:%M")
    done: List[str] = []
    for key, _ph in contract.THREAD_LAYERS:
        if key not in values:
            continue
        want = contract.name(key).lower()
        start, end = _where_span(lines)
        head = next((i for i in range(start, end) if lines[i].startswith("### ")
                     and lines[i][4:].strip().lower().startswith(want)), -1)
        if head < 0:
            continue
        stop = next((j for j in range(head + 1, end) if lines[j].startswith("### ")), end)
        body = [ln for ln in lines[head + 1:stop] if not ln.strip().startswith("<")]
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        old = []
        if key in REPLACE_KEYS:  # F65: «где мы» — снимок, а не лента
            old, body = [ln for ln in body if ln.strip()], []
        if key in REPLACE_KEYS:  # «где мы» — снимок: штамп всегда время записи
            body.append("- {0} — {1}".format(stamp, values[key]))
        else:
            body.append(stamp_line(values[key], now, tg_times))
        tail = [""] if stop < len(lines) else []
        lines = lines[:head + 1] + body + tail + lines[stop:]
        if old:
            lines = _journal(lines, contract.name(key), stamp, old)
        done.append(contract.name(key))
    _io.atomic_write(goal_path, "\n".join(lines).rstrip("\n") + "\n")
    if "expectation" in values:
        fields.set_field(goal_path, EXPECT_AT_FIELD, now.isoformat(timespec="seconds"))
    return done


# --- F66: свежесть слоёв против ПЕРЕДАЧИ ---------------------------------------

_STAMP_RE = re.compile(r"^\s*[-*]\s*(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})\s+—")
_PULSE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\S*\s+—\s+(.*)$")


def _layer_lines(goal: Path, want: str) -> List[str]:
    try:
        lines = goal.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    start, end = _where_span(lines)
    out: List[str] = []
    head = ""
    for ln in (lines[start:end] if start >= 0 else []):
        if ln.startswith("### "):
            head = ln[4:].strip().lower()
        elif head.startswith(want) and ln.strip() and not ln.strip().startswith("<"):
            out.append(ln)
    return out


def _mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except OSError:
        return None


def _pulses(thread_dir: Path):
    """``(время, нота)`` пульсов всех сессий нити (``arcs/*/arc.md`` → ``## context``)."""
    for arc in sorted((Path(thread_dir) / "arcs").glob("*/arc.md")):
        try:
            text = arc.read_text(encoding="utf-8")
        except OSError:
            continue
        section = ""
        for ln in text.splitlines():
            if ln.startswith("## "):
                section = ln.strip().lower()
                continue
            m = _PULSE_RE.match(ln.strip()) if section == "## context" else None
            if m:
                try:
                    yield datetime.fromisoformat(m.group(1)), m.group(2)
                except ValueError:
                    continue


def freshness(thread_dir: Path) -> Dict[str, object]:
    """Свежесть слоя ожидания и «главного» против пульсов нити (SOC-101 F66).

    * ``expect`` — ``empty`` | ``stale`` (слой правлен раньше самого свежего пульса
      с меткой «Ожидание Гриши:») | ``fresh``; время слоя — новейший штамп
      ``ДД.ММ ЧЧ:ММ`` его строк, нет штампов — дата правки goal-файла;
    * ``main`` — ``empty`` | ``stale`` (plan.md правлен раньше последней ПЕРЕДАЧИ)
      | ``fresh``;
    * ``expect_at`` / ``expect_pulse_at`` / ``main_at`` / ``pass_at`` — времена."""
    thread_dir = Path(thread_dir)
    goal = _stream.passport_path(thread_dir)
    expect_pulse = pass_at = None
    for at, note in _pulses(thread_dir):
        if "expectation" in contract.pulse_labels(note):
            expect_pulse = max(expect_pulse or at, at)
        if contract.is_pass_note(note):
            pass_at = max(pass_at or at, at)
    lines = _layer_lines(goal, contract.name("expectation").lower())
    expect_at = None
    year = (expect_pulse or datetime.now()).year
    for ln in lines:
        m = _STAMP_RE.match(ln)
        if m:
            try:
                at = datetime(year, int(m.group(2)), int(m.group(1)),
                              int(m.group(3)), int(m.group(4)))
            except ValueError:
                continue
            expect_at = max(expect_at or at, at)
    written = (fields.read_field(goal, EXPECT_AT_FIELD) or "").strip() if lines else ""
    try:
        w_at = datetime.fromisoformat(written) if written else None
    except ValueError:
        w_at = None
    if w_at is not None:  # F81: строки несут время слов, а не записи
        expect_at = max(expect_at or w_at, w_at)
    if lines and expect_at is None:
        expect_at = _mtime(goal)
    if not lines:
        expect = "empty"
    elif expect_pulse and (expect_at is None or expect_at < expect_pulse.replace(
            second=0, microsecond=0)):
        expect = "stale"
    else:
        expect = "fresh"
    main_at = _mtime(thread_dir / _plan_main.PLAN_FILE)
    if not _plan_main.live_lines(thread_dir):
        main = "empty"
    elif pass_at and (main_at is None or main_at < pass_at):
        main = "stale"
    else:
        main = "fresh"
    return {"expect": expect, "expect_at": expect_at, "expect_pulse_at": expect_pulse,
            "main": main, "main_at": main_at, "pass_at": pass_at}


def cmd_layer(args) -> int:
    """``tide thread layer <нить> --expect "…" --where "…" --exp "…" --main "…"``."""
    target = (getattr(args, "target", None) or "").strip()
    values = {key: getattr(args, "layer_" + flag, None) or ""
              for flag, key in LAYER_FLAGS}
    main = split_main(getattr(args, "layer_main", None) or [])
    if not target or not (main or any(v.strip() for v in values.values())):
        raise LayersError("thread layer: назови нить и хотя бы один слой — " +
                          layer_cmd(target or "<нить>") + "  (или " +
                          main_cmd(target or "<нить>") + ")")
    entry = thread_entry(paths.require_tide_root(), target)
    if any(v.strip() for v in values.values()):
        goal = _stream.passport_path(entry)
        from . import dialog as _dialog
        nums = tg_refs(values.get("expectation") or "") + tg_refs(
            values.get("experience") or "")
        done = write_layers(goal, values, tg_times=_dialog.tg_times(nums, entry))
        print("tide: {0} — вписано: {1}".format(goal, ", ".join(done) or "ничего"))
    if main:
        replace = bool(getattr(args, "layer_replace", False))
        added = write_main(entry, main, replace=replace)
        print("tide: {0} — «главное» {1}: {2}".format(
            entry / _plan_main.PLAN_FILE, "заменено" if replace else "дописано",
            "; ".join(added) or "ничего нового (строки уже есть)"))
        problem = _plan_main.problem(entry)
        if problem:
            print("tide: «главное»: " + problem)
    empty = empty_layers(entry)
    print("tide: слои нити {0}: {1}".format(
        entry.name, "пусты — " + ", ".join(empty) if empty else "заполнены"))
    return 0


def seed_skeleton(goal_path: Path) -> List[str]:
    """Досеять каркас недостающих слоёв в goal-файл нити; вернуть добавленные имена.

    Секции ``## where we are`` нет — она дописывается в конец. Есть — недостающие
    ``### <слой>`` с заглушкой встают в её конец. Ничего другого не трогает:
    заполненные слои, старые заглушки и прочие секции остаются как были.
    """
    goal_path = Path(goal_path)
    text = goal_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start, end = _where_span(lines)
    have = _layer_heads(lines[start:end]) if start >= 0 else []
    missing = [(contract.name(k), ph) for k, ph in contract.THREAD_LAYERS
               if not any(h.startswith(contract.name(k).lower()) for h in have)]
    if not missing:
        return []
    add: List[str] = []
    for name, ph in missing:
        add += ["### {0}".format(name), ph, ""]
    add = add[:-1]  # без хвостовой пустой строки
    if start < 0:
        body = text.rstrip("\n") + "\n\n" + WHERE_HEAD + "\n" + "\n".join(add) + "\n"
    else:
        while end > start and not lines[end - 1].strip():
            end -= 1  # встаём после последней живой строки секции
        tail = lines[end:]
        while tail and not tail[0].strip():
            tail = tail[1:]
        head = lines[:end] + ([""] if end > start else [])
        body = "\n".join(head + add + ([""] + tail if tail else [])) + "\n"
    _io.atomic_write(goal_path, body)
    return [n for n, _ph in missing]


def thread_entry(root: Path, ref: str) -> Path:
    """Открытая нить проекта по имени (``31-@main``, ``main``)."""
    entry = resolve.open_top_entry(Path(root), ref)
    if entry is None or not _stream.is_thread(entry):
        raise LayersError("thread layers: нет открытой нити {0!r} в {1}".format(
            ref, paths.arcs_dir(Path(root))))
    return entry


def cmd_layers(args) -> int:
    """``tide thread layers <нить> [--seed-skeleton]``."""
    target = (getattr(args, "target", None) or "").strip()
    if not target:
        raise LayersError("thread layers: назови нить — tide thread layers <нить>")
    entry = thread_entry(paths.require_tide_root(), target)
    goal = _stream.passport_path(entry)
    if getattr(args, "seed_skeleton", False):
        added = seed_skeleton(goal)
        print("tide: {0} — {1}".format(goal, "досеян каркас: " + ", ".join(added)
                                       if added else "каркас трёх слоёв уже есть"))
    empty = empty_layers(entry)
    print("tide: слои нити {0}: {1}".format(
        entry.name, "пусты — " + ", ".join(empty) if empty else "заполнены"))
    return 0


__all__ = ["LAYER_FLAGS", "LayersError", "cmd_layer", "cmd_layers", "empty_layers",
           "exit_warning", "freshness", "layer_cmd", "main_cmd", "seed_skeleton", "split_main",
           "thread_entry", "write_layers", "write_main"]

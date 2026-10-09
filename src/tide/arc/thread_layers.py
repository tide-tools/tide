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

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import handoff_contract as contract, io as _io, paths, resolve
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
    return ('tide thread layer {0} --expect "<чего ждёт Гриша — его словами, tg N>" '
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
    режется по « · », пробелы схлопываются, пустые и повторы уходят."""
    out: List[str] = []
    for v in values or []:
        for part in " ".join(str(v or "").split()).split(" · "):
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


def write_layers(goal_path: Path, values: Dict[str, str], *,
                 now: Optional[datetime] = None) -> List[str]:
    """Вписать строки в слои ``## where we are`` goal-файла нити (SOC-101 F53).

    *values* — ``{ключ слоя контракта: текст}`` (``expectation`` / ``where`` /
    ``experience``). Каркаса нет — досевается (:func:`seed_skeleton`). Строка
    ``- ДД.ММ ЧЧ:ММ — текст`` встаёт в конец слоя, заглушка ``<…>`` слоя уходит,
    живые строки остаются. Ровно те секции, что читают :func:`empty_layers` и
    сборщик сида. Вернуть имена слоёв, куда вписано."""
    goal_path = Path(goal_path)
    values = {k: " ".join(str(v or "").split()) for k, v in values.items()}
    values = {k: v for k, v in values.items() if v}
    if not values:
        return []
    seed_skeleton(goal_path)
    lines = goal_path.read_text(encoding="utf-8").splitlines()
    stamp = (now or datetime.now()).strftime("%d.%m %H:%M")
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
        body.append("- {0} — {1}".format(stamp, values[key]))
        tail = [""] if stop < len(lines) else []
        lines = lines[:head + 1] + body + tail + lines[stop:]
        done.append(contract.name(key))
    _io.atomic_write(goal_path, "\n".join(lines).rstrip("\n") + "\n")
    return done


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
        done = write_layers(goal, values)
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
           "exit_warning", "layer_cmd", "main_cmd", "seed_skeleton", "split_main",
           "thread_entry", "write_layers", "write_main"]

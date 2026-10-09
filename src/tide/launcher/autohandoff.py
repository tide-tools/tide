"""tide.launcher.autohandoff — ``tide handoff continue --auto``: передача без пересказа.

Когда контекст сессии переполнен (Stop-хук ``tide hook context-watch``), агент
передаёт нить одной командой. Посев собирается НЕ из памяти уставшей сессии, а
с диска арки — то, что уже записано по ходу:

* паспорт сессии — курсор, «дальше», хвост пульса (``## context``);
* решения нити (``decisions.md``) и её живые работы;
* открытые (неотмеченные) пункты работ нити;
* хвост журнала диалога с человеком — его ведёт пульт, путь из
  ``$TIDE_DIALOG_LOG`` или поля ``dialog-log:`` нити (:mod:`tide.arc.dialog`);
  нет журнала — посев без хвоста.

Дальше — готовый путь хендоффа (оффер → ▶ → ``ao spawn``). Новая сессия
наследует счётчик цепочки ``auto-chain`` и отметку прогресса ``progress-mark``.

**Условие остановки** (вердикт 09.10): новую сессию НЕ поднимаем, если

1. в нити висит вопрос к человеку — артефакт ``question`` со статусом ``new``;
2. три автопередачи подряд прошли без продвижения — не прибавилось ни отмеченных
   пунктов работ нити, ни актов (``акт: …`` в паспортах её сессий).

Тогда хендофф делается без подъёма (оффер висит для ▶), а Грише идёт сигнал
«нить встала»: артефакт на стол доски + внешняя команда из ``$TIDE_ALERT_CMD``
с ``TIDE_ALERT_ROUTE=human``, если она задана (адресов человека пакет tide не
знает). Ту же команду зовёт сторож контекста (route ``log`` / ``manager``).

Чистые функции (``count_progress``, ``open_questions``, ``decide``,
``build_auto_seed``) — без argparse; :func:`run_auto` — оркестрация.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import acts, fields, offload, paths, slug
from ..arc import artifact as _artifact, work as _work
from ..arc.stream import StreamError

# Сколько автопередач подряд без продвижения терпим (развилка Г-1).
CHAIN_LIMIT = 3

# Поля паспорта сессии.
AUTO_CHAIN_FIELD = "auto-chain"        # автопередач подряд без продвижения до этой сессии
PROGRESS_MARK_FIELD = "progress-mark"  # пункты+акты нити на момент рождения сессии
HANDED_OFF_FIELD = "handed-off"        # эта сессия уже передала нить (хук молчит)

ALERT_CMD_ENV = "TIDE_ALERT_CMD"
ROUTE_HUMAN = "human"  # «нить встала» — Грише; лестница контекста шлёт log/manager

DIALOG_TAIL = 12  # = seed_draft.DIALOG_TAIL (хвост диалога в сиде)

# Отметки причины «не поднимаем».
STOP_QUESTION = "question"
STOP_STALLED = "stalled"


class AutoHandoffError(StreamError):
    """Ошибка пользователю: нет своей сессии, некуда целиться."""


# --- чтение нити -------------------------------------------------------------

def thread_of(session_entry: Path) -> Path:
    """``…/NN-@thread/arcs/NN-session`` → папка нити."""
    return Path(session_entry).parents[1]


def _project_root(session_entry: Path) -> Path:
    root = paths.find_tide_root(Path(session_entry))
    if root is None:
        raise AutoHandoffError("handoff --auto: {0} не внутри tide-проекта".format(
            session_entry))
    return root


def count_progress(root: Path, thread_dir: Path) -> int:
    """Продвижение нити одним числом: отмеченные пункты её работ + акты её сессий.

    Работы считаются ВСЕ (и закрытые): закрытие работы не должно выглядеть как
    откат. Акты — только настоящие (``акт: …``), не пульс: пульс просит сам хук
    контекста, и считай мы его — предохранитель не сработал бы никогда.
    """
    done = 0
    try:
        for rec in _work.thread_works(root, Path(thread_dir).name, live_only=False):
            done += int(rec.get("done") or 0)
    except StreamError:
        pass
    kind = acts.KIND + ":"
    done += sum(1 for a in acts.read_acts(Path(thread_dir)) if a.text.startswith(kind))
    return done


def _thread_matches(from_arc: str, thread_dir: Path) -> bool:
    tail = (from_arc or "").strip().rstrip("/").rsplit("/", 1)[-1]
    if not tail:
        return False
    return (slug.entry_slug(tail) or tail) == slug.entry_slug(Path(thread_dir).name)


def _meta(text: str, key: str) -> str:
    m = re.search(r"^{0}:[ \t]*(.*)$".format(re.escape(key)), text, re.M)
    return m.group(1).strip() if m else ""


def open_questions(root: Path, thread_dir: Path) -> List[str]:
    """Вопросы к человеку из этой нити, ещё не отвеченные: ``["NN-slug — подпись"]``."""
    out: List[str] = []
    adir = _artifact.artifacts_dir(root)
    if not adir.is_dir():
        return out
    for p in sorted(adir.iterdir()):
        f = p / "artifact.md"
        if not p.is_dir() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        # artifact.md keeps its fields under the H1 — read them like artifact.py does
        if _meta(text, "kind") != "question" or _meta(text, "status") != _artifact.NEW:
            continue
        if not _thread_matches(_meta(text, "from-arc"), thread_dir):
            continue
        out.append("{0} — {1}".format(p.name, _artifact._head_caption(text)))
    return out


def open_items(root: Path, thread_dir: Path) -> List[str]:
    """Неотмеченные согласованные пункты живых работ нити: ``работа 66 · 3. тайтл``."""
    out: List[str] = []
    wdir = _work.works_dir(root)
    try:
        recs = _work.thread_works(root, Path(thread_dir).name)
    except StreamError:
        return out
    for rec in recs:
        f = wdir / str(rec["slug"]) / "work.md"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for n, (state, title) in enumerate(_work.all_items(text), start=1):
            if state == " ":
                out.append("- работа {0} · {1}. {2}".format(rec["num"], n, title))
    return out


def _int_field(passport: Path, key: str) -> Optional[int]:
    raw = (fields.read_field(passport, key) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


# --- решение -----------------------------------------------------------------

@dataclass
class Decision:
    """Что делать с передачей: поднимать или встать, и что унаследует преемник."""

    spawn: bool
    reason: str = ""                 # STOP_QUESTION / STOP_STALLED / ""
    detail: str = ""                 # человеческим языком
    chain: int = 1                   # auto-chain преемника
    progress: int = 0                # progress-mark преемника
    questions: List[str] = field(default_factory=list)


def decide(*, origin_chain: Optional[int], origin_mark: Optional[int],
           progress: int, questions: List[str],
           limit: int = CHAIN_LIMIT) -> Decision:
    """Поднимать ли новую сессию (чистое).

    Цепочка: ``auto-chain`` преемника = цепочка родителя + 1, если с рождения
    родителя нить не продвинулась (``progress <= origin_mark``); иначе 1.
    Родитель без отметки (его подняли руками) — продвижение неизвестно, считаем
    за начало цепочки. Встаём, когда родитель уже сам был ``limit``-й передачей
    подряд без продвижения и снова ничего не сдвинул.
    """
    stalled_hop = origin_mark is not None and progress <= origin_mark
    chain = (origin_chain or 0) + 1 if stalled_hop else 1
    d = Decision(spawn=True, chain=chain, progress=progress, questions=list(questions))
    if questions:
        d.spawn, d.reason = False, STOP_QUESTION
        d.detail = "в нити висит вопрос к тебе: {0}".format("; ".join(questions))
    elif stalled_hop and (origin_chain or 0) >= limit:
        d.spawn, d.reason = False, STOP_STALLED
        d.detail = ("{0} автопередачи подряд без продвижения — ни пунктов работ, "
                    "ни актов не прибавилось".format(origin_chain))
    return d


def decide_for(session_entry: Path) -> Decision:
    """:func:`decide` по диску: паспорт сессии + её нить."""
    root = _project_root(session_entry)
    thread = thread_of(session_entry)
    passport = Path(session_entry) / "arc.md"
    return decide(origin_chain=_int_field(passport, AUTO_CHAIN_FIELD),
                  origin_mark=_int_field(passport, PROGRESS_MARK_FIELD),
                  progress=count_progress(root, thread),
                  questions=open_questions(root, thread))


# --- посев -------------------------------------------------------------------

def build_auto_seed(session_entry: Path) -> str:
    """Посев автохендоффа из арки — без пересказа чата.

    Все блоки контракта сида (:mod:`tide.handoff_contract`) — тот же сборщик, что у
    ``tide handoffs draft``: ожидание Гриши и «где мы» — слои нити дословно (F1/F2)
    плюс курсор и итог прошлой сессии; опыт и карта входа наследуются из входного
    сида (F3, F4); открытые пункты работ нити — «в работе прямо сейчас»; хвост
    диалога ЭТОЙ нити — «неотвеченные реплики» (F6); сырой журнал пульса — не
    больше :data:`seed_draft.JOURNAL_SHARE` тела (F5). Подсказки куратора вырезаны.
    """
    from . import seed_draft as sd

    session_entry = Path(session_entry)
    root = _project_root(session_entry)
    thread = thread_of(session_entry)
    passport = sd.sections(sd._read(session_entry / "arc.md"))
    context = sd.section_body(passport, "context")

    def bodies_at(clip: int) -> Dict[str, List[str]]:
        out = sd.draft_bodies(root, session_entry, pulse_clip=clip)
        out = {k: [ln for ln in v if "<!--" not in ln] for k, v in out.items()}
        # пустая карта — «—» и ОДИН указатель (абсолютные пути); не пустая — карта
        # и тот же указатель последней строкой перед топиком
        pointer = ("указатель: паспорт прошлой сессии {0} (курсор, ## context), plan.md "
                   "нити {1}".format(session_entry.resolve() / "arc.md",
                                     thread.resolve() / "plan.md"))
        emap = out[sd.ENTRY_MAP]
        topic = [ln for ln in emap if ln.lstrip("- ").startswith("топик:")]
        real = [ln for ln in emap if ln not in topic]
        out[sd.ENTRY_MAP] = (real + ["- " + pointer] if real else
                             ["{0} карту входа не курировали — {1}".format(sd.EMPTY, pointer)]
                             ) + topic
        return out

    bodies = bodies_at(0)
    nxt = [ln.lstrip("- ").strip() for ln in bodies.get(sd.NEXT, []) if ln.strip()]
    header = [
        "# автохендофф — {0} · нить {1}".format(session_entry.name, thread.name),
        "",
        "Собран с диска арки (`tide handoff continue --auto`), не из пересказа чата.",
    ]
    if nxt:
        # _pickup_goal берёт цель новой сессии отсюда
        header.append("следующий шаг: {0}".format(nxt[0]))
    head = "\n".join(header)
    # хвост пульса в «где мы» — тоже сырой журнал (F5), но режется только по
    # бюджету (F16), а последний пульс и ПЕРЕДАЧА — никогда (F24)
    return sd.seal_budgeted(bodies_at, context=context, header=head,
                            source="{0} (## context)".format(session_entry / "arc.md"))


# --- сигнал ------------------------------------------------------------------

def signal_stall(root: Path, thread_dir: Path, detail: str, *, offer: str = "",
                 source: str = "",
                 now: Optional[datetime] = None) -> Dict[str, object]:
    """«Нить встала»: артефакт на стол доски + ``$TIDE_ALERT_CMD`` с
    ``TIDE_ALERT_ROUTE=human``, если задана (:func:`send_alert`).

    Никогда не падает: сигнал — уведомление, не шаг, от которого зависит передача.
    Возвращает ``{"artifact": путь|None, "alert": код возврата|None}``.
    """
    tslug = slug.entry_slug(Path(thread_dir).name)
    caption = "нить {0} встала: нужен ты".format(tslug)
    how = ("оффер {0} висит — ▶ на доске или tide pickup {0}, когда разберёшься"
           .format(offer) if offer else "новую сессию не поднимал")
    content = "{0}.\n{1}.".format(detail, how)
    out: Dict[str, object] = {"artifact": None, "alert": None}
    try:
        out["artifact"] = _artifact.new_artifact(
            root, caption, "message", content, from_arc=tslug, now=now)
    except Exception:  # noqa: BLE001 — a signal must never break the handoff
        pass
    text = "{0} — {1}".format(caption, content.replace("\n", " "))
    out["alert"] = send_alert(text, route=ROUTE_HUMAN, source=source or tslug,
                              extra_env={"TIDE_ALERT_THREAD": tslug})
    return out


def send_alert(text: str, *, route: str, source: str = "",
               extra_env: Optional[Dict[str, str]] = None) -> Optional[int]:
    """Вызвать ``$TIDE_ALERT_CMD`` с *text* последним аргументом.

    Env команды: ``TIDE_ALERT_TEXT`` (тот же текст), ``TIDE_ALERT_ROUTE`` (кому:
    ``log`` — только в лог пульта, ``manager`` — менеджеру, ``human`` — Грише),
    ``TIDE_ALERT_SOURCE`` (``<проект>/<нить>/<сессия>``). Нет команды — None, молча.
    Никогда не падает: код возврата, -1 — команда не запустилась.
    """
    cmd = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if not cmd:
        return None
    env = dict(os.environ, TIDE_ALERT_TEXT=text, TIDE_ALERT_ROUTE=route,
               TIDE_ALERT_SOURCE=source, **(extra_env or {}))
    try:
        proc = subprocess.run(shlex.split(cmd) + [text], env=env, timeout=30,
                              capture_output=True, text=True)
        return proc.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


# --- оркестрация -------------------------------------------------------------

def caller_session_entry(root: Path, ref: Optional[str] = None) -> Path:
    """Своя сессия: *ref* (арка-сессия) или по ``$CLAUDE_CODE_SESSION_ID``."""
    from . import seed_draft

    try:
        return seed_draft.resolve_session(root, ref)
    except StreamError as exc:
        raise AutoHandoffError(str(exc).replace("handoffs draft", "handoff --auto"))


@dataclass
class AutoResult:
    session: Path
    thread: Path
    decision: Decision
    seed: str
    handoff: Optional[object] = None        # HandoffResult, None на dry-run
    signal: Optional[Dict[str, object]] = None


def run_auto(root: Path, *, mode: str = "continue", session_ref: Optional[str] = None,
             dry_run: bool = False, spawn: bool = True,
             adapter_name: Optional[str] = None) -> AutoResult:
    """Собрать посев, решить «поднимать / встать», передать.

    *dry_run* ничего не пишет (ни оффера, ни дистилла, ни сигнала) — только
    показывает решение и посев.
    """
    from . import handoff as _handoff

    entry = caller_session_entry(Path(root), session_ref)
    thread = thread_of(entry)
    decision = decide_for(entry)
    seed = build_auto_seed(entry)
    res = AutoResult(session=entry, thread=thread, decision=decision, seed=seed)
    if dry_run:
        return res
    owner = _project_root(entry)
    sid = (fields.read_field(entry / "arc.md", offload.CLAUDE_SESSION_FIELD) or "").strip()
    born = {AUTO_CHAIN_FIELD: str(decision.chain),
            PROGRESS_MARK_FIELD: str(decision.progress)}
    res.handoff = _handoff.run_handoff(
        owner, arc_ref=thread.name, mode=mode, summary=seed,
        from_session=sid or None, spawn=spawn and decision.spawn,
        adapter_name=adapter_name, born_fields=born)
    offer = getattr(res.handoff, "offer_path", None)
    stamp = datetime.now().isoformat(timespec="seconds")
    tried = getattr(res.handoff, "spawn", None)
    if not (tried and not res.handoff.handed_over):
        # подъём пробовали и он не вышел (в т.ч. принимающая не подала сигнала,
        # SOC-101 F25) — нить у этой сессии: «уже передала» не штампуем
        fields.set_field(entry / "arc.md", HANDED_OFF_FIELD,
                         "{0} → {1}".format(stamp, offer.stem if offer else mode))
    if not decision.spawn:
        res.signal = signal_stall(owner, thread, decision.detail,
                                  offer=offer.stem if offer else "",
                                  source="{0}/{1}/{2}".format(
                                      owner.name, slug.entry_slug(thread.name),
                                      slug.entry_slug(entry.name)))
    return res


def render_dry_run(res: AutoResult) -> str:
    d = res.decision
    verdict = ("поднял бы новую сессию" if d.spawn
               else "встал бы без подъёма: {0}".format(d.detail))
    lines = [
        "tide: handoff --auto (dry-run) — {0}".format(res.session.name),
        "  решение: {0}".format(verdict),
        "  преемник унаследует: {0}: {1} · {2}: {3}".format(
            AUTO_CHAIN_FIELD, d.chain, PROGRESS_MARK_FIELD, d.progress),
        "  ничего не записано: ни оффера, ни дистилла, ни сигнала",
        "",
        res.seed.rstrip(),
    ]
    return "\n".join(lines)


__all__ = ["AUTO_CHAIN_FIELD", "AutoHandoffError", "AutoResult", "CHAIN_LIMIT",
           "Decision", "HANDED_OFF_FIELD", "PROGRESS_MARK_FIELD", "build_auto_seed",
           "count_progress", "decide", "decide_for", "open_items", "open_questions",
           "render_dry_run", "run_auto", "send_alert", "signal_stall"]

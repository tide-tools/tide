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
   пунктов работ нити, ни содержательных пульсов её сессий (акты — тоже пульсы;
   механика приёма и передачи не считается, см. :func:`count_progress`).

Тогда хендофф делается без подъёма (оффер висит для ▶), а сигнал «нить встала»
идёт сразу тремя путями: артефакт на стол доски, внешняя команда из
``$TIDE_ALERT_CMD`` с ``TIDE_ALERT_ROUTE=human`` (если задана) и менеджеру —
``ao send`` по адресу ``tide whois manager`` (контракт v1: стоп не молчит).
Ту же внешнюю команду зовёт сторож контекста (route ``log`` / ``manager``).

**Автоподъём** (слово менеджера 09.10): вставшая нить не стоит вечно. Сигнал
запускает отсоединённый ``tide handoffs autoraise <оффер> --after N``
(``$TIDE_AUTORAISE_AFTER``, по умолчанию 1800 с; ``0``/``off`` — выключено). Он
опрашивает оффер раз в :data:`AUTORAISE_POLL` с: взяли/сняли — тихо выходит
сразу; через N секунд оффер всё ещё висит — тот же подъём, что ``tide pickup``,
и сообщение менеджеру (``ao send``). Грише (route=human) — только если поднять не
вышло (нить всё ещё стоит); удачный подъём — route=log, в журнал пульта (слово
Гриши tg 3733: о приёме и хендоффе ему не писать, только доска и журнал).

Чистые функции (``count_progress``, ``open_questions``, ``decide``,
``build_auto_seed``) — без argparse; :func:`run_auto` — оркестрация.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
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
PROGRESS_MARK_FIELD = "progress-mark"  # пункты+пульсы нити на момент рождения сессии
HANDED_OFF_FIELD = "handed-off"        # эта сессия уже передала нить (хук молчит)

ALERT_CMD_ENV = "TIDE_ALERT_CMD"
# SOC-179: явный признак срочности для пульта. route=manager копится в пачку раз в 25 мин;
# urgent=1 — пачка уходит сразу (эскалация, шов-синк). Сирота, «закрыть?» — несрочно.
ALERT_URGENT_ENV = "TIDE_ALERT_URGENT"
AUTORAISE_ENV = "TIDE_AUTORAISE_AFTER"  # сек до автоподъёма вставшей нити; 0/off — нет
AUTORAISE_DEFAULT = 1800
AUTORAISE_POLL = 30  # как часто автоподъём смотрит, не взяли ли оффер
ROUTE_HUMAN = "human"  # «нить встала» — Грише; лестница контекста шлёт log/manager
ROUTE_LOG = "log"      # только журнал пульта — удачный автоподъём (tg 3733)

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


# Строки ``## context``, которые пишет МЕХАНИКА, а не работа сессии: приём оффера
# (handoff_queue.RECEPTION_PULSE), «приём не состоялся» старой сессии
# (launcher.handoff.NOT_ALIVE_LINE). Сторож контекста в паспорт не пишет (только
# поле ``ctx-ping:``), поэтому его пинги в счёт и так не попадают.
_MECHANICAL_PULSES = (
    "шов приёма закрыт механикой",
    "приём не состоялся — не замирай",
)


def is_mechanical_pulse(text: str) -> bool:
    """Строка пульса — механика или сама передача, не продвижение нити.

    Механика: приём оффера, «приём не состоялся». Передача: нота с меткой
    ``ПЕРЕДАЧА`` в начале (:func:`tide.handoff_contract.is_pass_note`) — её
    пишет каждая передающая сессия, и считай мы её, сессия, которая ничего не
    сделала, кроме приёма и передачи, давала бы прогресс (петля без стопа).
    """
    from .. import handoff_contract

    body = " ".join((text or "").split())
    if not body:
        return True
    if any(mark in body for mark in _MECHANICAL_PULSES):
        return True
    return handoff_contract.is_pass_note(body)


def count_progress(root: Path, thread_dir: Path) -> int:
    """Продвижение нити одним числом: отмеченные пункты её работ + содержательные
    пульсы всех её сессий.

    По контракту v1 работа живёт в Linear, а нити пишут о ней пульсами — поэтому
    продвижение видно по ``## context`` паспортов: каждая строка
    ``- <ISO-время> — …`` (акт — тоже такая строка), КРОМЕ механики
    (:func:`is_mechanical_pulse`). Защита от петли сохраняется: сессия, которая
    только приняла нить и передала её дальше, ничего не прибавляет.

    Монотонно: работы считаются ВСЕ (и закрытые) — закрытие работы не откат;
    пульсы — по ВСЕМ сессиям нити, закрытым тоже, а пульсы только дописываются.
    """
    done = 0
    try:
        for rec in _work.thread_works(root, Path(thread_dir).name, live_only=False):
            done += int(rec.get("done") or 0)
    except StreamError:
        pass
    done += sum(1 for a in acts.read_acts(Path(thread_dir))
                if not is_mechanical_pulse(a.text))
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
                    "ни содержательных пульсов не прибавилось".format(origin_chain))
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
        # plan.md — только если «главное» в нём есть (как F50: не звать в пустое)
        try:
            from ..arc import plan_main
            has_main = bool(plan_main.live_lines(thread))
        except Exception:  # noqa: BLE001
            has_main = False
        pointer = "указатель: паспорт прошлой сессии {0} (курсор, ## context)".format(
            session_entry.resolve() / "arc.md")
        if has_main:
            pointer += ", plan.md нити {0}".format(thread.resolve() / "plan.md")
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
    # сид менеджера — без сырого журнала, строкой-указателем (Гриша «Давай А», SOC-143)
    return sd.seal_budgeted(bodies_at, context=context, header=head,
                            source="{0} (## context)".format(session_entry / "arc.md"),
                            journal_off=sd.journal_pointer(root, session_entry))


# --- сигнал ------------------------------------------------------------------

def signal_stall(root: Path, thread_dir: Path, detail: str, *, offer: str = "",
                 source: str = "", home: Optional[Path] = None,
                 now: Optional[datetime] = None) -> Dict[str, object]:
    """«Нить встала»: артефакт на стол доски + ``$TIDE_ALERT_CMD`` с
    ``TIDE_ALERT_ROUTE=human``, если задана (:func:`send_alert`), + сразу
    менеджеру ``ao send`` (:func:`tell_manager`) — стоп не молчит, даже когда
    внешней команды в env нет. Есть оффер — заводится автоподъём
    (:func:`schedule_autoraise`); *home* — control-home, где висит оффер.

    Никогда не падает: сигнал — уведомление, не шаг, от которого зависит передача.
    Возвращает ``{"artifact": путь|None, "alert": код|None, "manager": {...},
    "autoraise": {"after": сек, "pid": pid|None}}``.
    """
    tslug = slug.entry_slug(Path(thread_dir).name)
    caption = "нить {0} встала: нужен ты".format(tslug)
    plan = schedule_autoraise(home, offer, tslug) if offer else {"after": 0, "pid": None}
    how = ("оффер {0} висит — ▶ на доске или tide pickup {0}, когда разберёшься"
           .format(offer) if offer else "новую сессию не поднимал")
    if plan.get("pid"):
        how += "; без ответа через {0} мин подниму сам".format(int(plan["after"]) // 60)
    content = "{0}.\n{1}.".format(detail, how)
    out: Dict[str, object] = {"artifact": None, "alert": None, "manager": None,
                              "autoraise": plan}
    try:
        out["artifact"] = _artifact.new_artifact(
            root, caption, "message", content, from_arc=tslug, now=now)
    except Exception:  # noqa: BLE001 — a signal must never break the handoff
        pass
    text = "{0} — {1}".format(caption, content.replace("\n", " "))
    out["alert"] = send_alert(text, route=ROUTE_HUMAN, source=source or tslug,
                              extra_env={"TIDE_ALERT_THREAD": tslug})
    mtext = stall_manager_text(Path(root).name, tslug, detail, offer=offer)
    if plan.get("pid"):
        mtext += "; без ответа через {0} мин tide поднимет сам".format(
            int(plan["after"]) // 60)
    out["manager"] = tell_manager(mtext)
    return out


def stall_manager_text(project: str, thread: str, detail: str, *,
                       offer: str = "") -> str:
    """Менеджеру: какая нить встала, почему, номер оффера и команда подъёма."""
    head = "tide: нить {0}/{1} встала, новую сессию не поднял. Почему: {2}.".format(
        project, thread, (detail or "").rstrip("."))
    if offer:
        return "{0} Оффер {1} висит; подъём: tide pickup {1}".format(head, offer)
    return head + " Оффера нет."


def _ao_send(handle: str, text: str) -> Optional[int]:
    """``ao send --session <handle> --message <text>`` → код возврата.

    Бинарь — тот же, что у адаптера AO (:func:`tide.adapters.ao.ao_bin`,
    ``$TIDE_AO_BIN``). None — ao не установлен; -1 — не запустилась.
    Никогда не падает."""
    from ..adapters import ao as _ao

    if not _ao.ao_available():
        return None
    exe = _ao.ao_bin()
    try:
        proc = subprocess.run([exe, "send", "--session", handle, "--message", text],
                              capture_output=True, text=True, timeout=60)
        return proc.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


def tell_manager(text: str) -> Dict[str, object]:
    """Написать менеджеру (адрес — :func:`tide.whois.manager`, как ``tide whois
    manager``) через ``ao send``. Нет менеджера или ao — тихо пропускаем.

    → ``{"to": ao-id|None, "code": код|None}``. Никогда не падает."""
    try:
        from .. import whois
        rec = whois.manager()
    except Exception:  # noqa: BLE001 — no address is a skip, not a failure
        rec = {}
    handle = str(rec.get("ao_session") or "").strip()
    if not handle:
        return {"to": None, "code": None}
    try:
        code = _ao_send(handle, text)
    except Exception:  # noqa: BLE001
        code = -1
    return {"to": handle, "code": code}


def send_alert(text: str, *, route: str, source: str = "",
               extra_env: Optional[Dict[str, str]] = None,
               urgent: bool = False) -> Optional[int]:
    """Вызвать ``$TIDE_ALERT_CMD`` с *text* последним аргументом.

    Env команды: ``TIDE_ALERT_TEXT`` (тот же текст), ``TIDE_ALERT_ROUTE`` (кому:
    ``log`` — только в лог пульта, ``manager`` — менеджеру, ``human`` — Грише),
    ``TIDE_ALERT_SOURCE`` (``<проект>/<нить>/<сессия>``), ``TIDE_ALERT_URGENT=1`` —
    только при *urgent* (SOC-179: пульт не ждёт пачки, будит сразу; без признака
    ``route=manager`` ждёт пачки до 25 мин). Нет команды — None, молча.
    Никогда не падает: код возврата, -1 — команда не запустилась.
    """
    cmd = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if not cmd:
        return None
    env = dict(os.environ, TIDE_ALERT_TEXT=text, TIDE_ALERT_ROUTE=route,
               TIDE_ALERT_SOURCE=source, **(extra_env or {}))
    env.pop(ALERT_URGENT_ENV, None)       # не наследовать срочность из окружения
    if urgent:
        env[ALERT_URGENT_ENV] = "1"
    try:
        proc = subprocess.run(shlex.split(cmd) + [text], env=env, timeout=30,
                              capture_output=True, text=True)
        return proc.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


# --- автоподъём вставшей нити --------------------------------------------------

def autoraise_after() -> int:
    """Пауза автоподъёма из ``$TIDE_AUTORAISE_AFTER`` (сек); 0 — выключено."""
    raw = (os.environ.get(AUTORAISE_ENV) or "").strip().lower()
    if not raw:
        return AUTORAISE_DEFAULT
    if raw in ("0", "off", "no", "false", "нет"):
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return AUTORAISE_DEFAULT


def _spawn_autoraise(home: Path, offer: str, thread: str, after: int) -> Optional[int]:
    """Отсоединённый ``tide handoffs autoraise`` (своя сессия процессов — не
    умрёт вместе с передающей сессией) → pid."""
    argv = [sys.executable, "-m", "tide", "handoffs", "autoraise", offer,
            "--after", str(after), "--home", str(home), "--thread", thread]
    proc = subprocess.Popen(argv, cwd=str(home), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)
    return proc.pid


def schedule_autoraise(home: Optional[Path], offer: str, thread: str) -> Dict[str, object]:
    """Завести автоподъём оффера *offer*. Выключен / нет оффера / не вышло —
    ``pid: None``. Никогда не падает."""
    after = autoraise_after()
    out: Dict[str, object] = {"after": after, "pid": None}
    if not offer or after <= 0:
        return out
    try:
        where = Path(home) if home is not None else paths.control_home()
        out["pid"] = _spawn_autoraise(where, offer, thread, after)
    except Exception:  # noqa: BLE001 — a signal must never break the handoff
        out["pid"] = None
    return out


def _offer_state(home: Path, key: str):
    """``("offered"|"taken"|"gone", запись|None)`` — висит ли оффер *key* невзятым."""
    from .. import handoff_queue as hq

    rec = next((r for r in hq.list_offers(home) if r["name"] == key), None)
    if rec is None:
        return "gone", None
    taken = str(rec.get("taken_at") or "").strip() not in ("", "-")
    if rec.get("status") != hq.STATUS_OFFERED or taken:
        return "taken", rec
    return "offered", rec


def _raise_offer(home: Path, key: str, *, after: int = 0) -> Dict[str, object]:
    """Тот же подъём, что ``tide pickup <key>`` (:func:`tide.launcher.pickup.run_pickup`),
    но с ``raised-by: autoraise`` — оффер и промпт знают, что подняла не рука."""
    from . import pickup, raise_log

    try:
        return pickup.run_pickup(home, key, raised_by=raise_log.AUTORAISE,
                                 raised_after=after)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "failed", "detail": str(exc)}


def run_autoraise(home: Path, key: str, *, after: int, thread: str = "",
                  poll: int = AUTORAISE_POLL, sleep=time.sleep) -> Dict[str, object]:
    """Ждать *after* сек; оффер всё ещё не взят — поднять самому и сказать.

    Оффер смотрится каждые *poll* сек: взяли (``taken-at``) или сняли — выходим
    сразу и молча (процесс не висит после ответа). Резерв ▶ свежий — подъём уже
    идёт, тоже выходим. Иначе :func:`_raise_offer` + Грише (route=human) +
    менеджеру (``ao send``). Никогда не падает.
    """
    from .. import handoff_queue as hq
    from . import raise_log

    home = Path(home)
    waited = 0
    state, rec = _offer_state(home, key)
    # журнал подъёма (SOC-121): ожидание и решение автоподъёма — в state/raises.log
    raise_log.log(home, key, raise_log.AUTORAISE, "autoraise: жду {0} сек{1}".format(
        after, " · нить {0}".format(thread) if thread else ""))
    while state == "offered" and waited < after:
        step = min(max(1, poll), after - waited)
        sleep(step)
        waited += step
        state, rec = _offer_state(home, key)
    out: Dict[str, object] = {"key": key, "waited": waited, "state": state,
                              "action": "skip"}
    if state != "offered":
        dropped = state == "gone" or (rec or {}).get("status") == hq.STATUS_DROPPED
        raise_log.log(home, key, raise_log.AUTORAISE, "autoraise: оффер {0} через {1} сек "
                      "— выхожу".format("снят" if dropped else "взят", waited))
        return out
    reserved = str(rec.get("pickup_session") or "").strip()
    if reserved and reserved != "-" and not rec.get("pickup_stale"):
        out["state"] = "launching"
        raise_log.log(home, key, raise_log.AUTORAISE,
                      "autoraise: резерв свежий ({0}) — подъём уже идёт, выхожу".format(reserved))
        return out
    who = thread or slug.entry_slug(str(rec.get("arc") or "").rsplit("/", 1)[0]) or key
    mins = waited // 60
    raise_log.log(home, key, raise_log.AUTORAISE,
                  "autoraise: {0} сек без ответа — поднимаю сам".format(waited))
    res = _raise_offer(home, key, after=waited)
    out["action"] = "raised" if res.get("ok") else "failed"
    out["pickup"] = res
    if res.get("ok"):
        text = ("tide: нить {0} стояла {1} мин без ответа — поднял новую сессию сам, "
                "оффер {2}".format(who, mins, key))
    else:
        text = ("tide: нить {0} стоит {1} мин без ответа, оффер {2} — поднять сам не "
                "вышло ({3}); подними: tide pickup {2}".format(
                    who, mins, key, res.get("detail") or "без подробностей"))
    out["text"] = text
    # tg 3733: удачный подъём — хендофф, Грише не пишем (журнал пульта + менеджер);
    # не вышло — нить всё ещё стоит, это продолжение «нить встала» → Грише.
    route = ROUTE_LOG if res.get("ok") else ROUTE_HUMAN
    out["alert"] = send_alert(text, route=route, source="{0}/{1}".format(
        str(rec.get("project") or ""), who), extra_env={"TIDE_ALERT_THREAD": who})
    out["manager"] = tell_manager(text)
    return out


def _cmd_autoraise(args) -> int:
    import json

    from .. import paths as _paths

    home = Path(args.home) if args.home else _paths.control_home()
    out = run_autoraise(home, args.key, after=max(0, int(args.after)),
                        thread=args.thread or "")
    print(json.dumps({k: v for k, v in out.items() if k != "pickup"},
                     ensure_ascii=False, default=str))
    return 0


def register_autoraise(hsub) -> None:
    """Скрытая ``tide handoffs autoraise`` — её запускает :func:`schedule_autoraise`."""
    ap = hsub.add_parser("autoraise")
    ap.add_argument("key")
    ap.add_argument("--after", type=int, default=AUTORAISE_DEFAULT)
    ap.add_argument("--home", default=None)
    ap.add_argument("--thread", default="")
    ap.set_defaults(func=_cmd_autoraise, _cmd="handoffs autoraise")
    # не светить в help группы: внутренняя команда, не для рук
    hsub._choices_actions = [a for a in hsub._choices_actions if a.dest != "autoraise"]


# --- пульс ПЕРЕДАЧА перед автопередачей (SOC-101 F44) ------------------------

# Передача по порогу шла без пульса «ПЕРЕДАЧА» — и всё, что сборщик сида вешает на
# его метки (карта, ожидание Гриши, ждёт Гришу, в полёте, хвосты), молчало.
# Шаблон один на хук порога и на ``handoff --auto``.
PASS_FRESH_ENV = "TIDE_HANDOFF_FRESH_MIN"
PASS_FRESH_MIN = 30
PASS_TEMPLATE_NOTE = ("ПЕРЕДАЧА. Где мы: … · Ожидание Гриши: «…» (tg N) · Ждёт Гришу: … · "
                      "В полёте: … · Хвосты: … · Решения: … · Опыт: … · "
                      "Карта: <путь> — зачем")

_PULSE_STAMP = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\s*[—-]\s*(.*)$")


def session_ref(entry: Path) -> str:
    """``<нить>/<сессия>`` — адрес сессии для ``tide offload``."""
    entry = Path(entry)
    return "{0}/{1}".format(slug.entry_slug(entry.parent.parent.name),
                            slug.entry_slug(entry.name))


def pass_template(entry: Path) -> str:
    """Готовая команда пульса ПЕРЕДАЧА со всеми метками сида; курсор и «дальше» —
    тем же вызовом (их сборщик берёт в «где мы» и «следующий шаг»)."""
    return ('tide offload {0} --cursor "<где стоишь>" --next "<шаги через · >" "{1}"'
            .format(session_ref(entry), PASS_TEMPLATE_NOTE))


def pass_fresh_minutes() -> int:
    raw = (os.environ.get(PASS_FRESH_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return PASS_FRESH_MIN
    return value if value > 0 else PASS_FRESH_MIN


def fresh_pass_note(entry: Path, *, minutes: Optional[int] = None,
                    now: Optional[datetime] = None) -> Optional[str]:
    """Пульс ПЕРЕДАЧА сессии за последние *minutes* минут (текст) — или None."""
    from .. import handoff_contract

    minutes = pass_fresh_minutes() if minutes is None else minutes
    now = now or datetime.now()
    try:
        text = (Path(entry) / "arc.md").read_text(encoding="utf-8")
    except OSError:
        return None
    for ln in reversed(text.splitlines()):
        m = _PULSE_STAMP.match(ln.strip())
        if not m or not handoff_contract.is_pass_note(m.group(2)):
            continue
        try:
            at = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if (now - at).total_seconds() <= minutes * 60:
            return m.group(2)
    return None


def pass_warning(entry: Path, *, strict: bool = False,
                 now: Optional[datetime] = None) -> Optional[str]:
    """Нет свежего пульса ПЕРЕДАЧА — предупреждение с готовым шаблоном (None — есть)."""
    minutes = pass_fresh_minutes()
    if fresh_pass_note(entry, minutes=minutes, now=now) is not None:
        return None
    verdict = ("с --strict передачу не делаю" if strict
               else "передачу не останавливаю, но сид соберётся без меток")
    return ("tide: ⚠ нет пульса ПЕРЕДАЧА за последние {0} мин — новая сессия не узнает "
            "из сида ожидание Гриши, что ждёт его, что в полёте, хвосты и карту ({1}). "
            "Сначала:\n  {2}\nзатем снова tide handoff continue --auto.".format(
                minutes, verdict, pass_template(entry)))


# --- оркестрация -------------------------------------------------------------

def layers_warning(thread_dir: Path) -> Optional[str]:
    """Слои нити «ожидание / где мы / опыт» пусты — предупреждение на передаче
    (SOC-101, acceptance-01 §12 п.1). Никогда не падает: передача важнее."""
    from ..arc import thread_layers

    try:
        return thread_layers.exit_warning(thread_dir)
    except Exception:  # noqa: BLE001
        return None


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
    warning: Optional[str] = None           # слои нити пусты (SOC-101) — не блок
    pass_warning: Optional[str] = None      # нет свежего пульса ПЕРЕДАЧА (F44)
    signal: Optional[Dict[str, object]] = None


def run_auto(root: Path, *, mode: str = "continue", session_ref: Optional[str] = None,
             dry_run: bool = False, spawn: bool = True,
             adapter_name: Optional[str] = None, strict: bool = False) -> AutoResult:
    """Собрать посев, решить «поднимать / встать», передать.

    *dry_run* ничего не пишет (ни оффера, ни дистилла, ни сигнала) — только
    показывает решение и посев. Нет пульса ПЕРЕДАЧА за :func:`pass_fresh_minutes`
    минут — предупреждение с шаблоном в stderr (F44); *strict* — отказ (на
    dry-run только предупреждение: он и так ничего не пишет).
    """
    from . import handoff as _handoff

    entry = caller_session_entry(Path(root), session_ref)
    pass_warn = pass_warning(entry, strict=strict)
    if pass_warn:
        print(pass_warn, file=sys.stderr)
        if strict and not dry_run:
            raise AutoHandoffError(
                "handoff --auto --strict: нет пульса ПЕРЕДАЧА за последние {0} мин — "
                "передача отменена, сначала пульс по шаблону выше".format(
                    pass_fresh_minutes()))
    thread = thread_of(entry)
    decision = decide_for(entry)
    seed = build_auto_seed(entry)
    res = AutoResult(session=entry, thread=thread, decision=decision, seed=seed,
                     warning=layers_warning(thread), pass_warning=pass_warn)
    if res.warning:
        print(res.warning, file=sys.stderr)  # предупреждение, не блок
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
                                  home=offer.parents[2] if offer else None,
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
    ]
    if res.pass_warning:
        lines.append("  ⚠ пульса ПЕРЕДАЧА нет — шаблон: {0}".format(
            pass_template(res.session)))
    lines += [
        "",
        res.seed.rstrip(),
    ]
    return "\n".join(lines)


__all__ = ["AUTO_CHAIN_FIELD", "AutoHandoffError", "AutoResult", "CHAIN_LIMIT",
           "Decision", "HANDED_OFF_FIELD", "PASS_FRESH_ENV", "PASS_TEMPLATE_NOTE",
           "PROGRESS_MARK_FIELD", "build_auto_seed", "fresh_pass_note", "pass_template",
           "pass_warning", "session_ref",
           "count_progress", "decide", "decide_for", "open_items", "open_questions",
           "is_mechanical_pulse", "layers_warning", "register_autoraise",
           "run_autoraise", "schedule_autoraise", "render_dry_run", "run_auto",
           "send_alert", "signal_stall", "stall_manager_text", "tell_manager"]

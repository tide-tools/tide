"""tide.truth_edits — журнал правок записей решений и фактов (SOC-130, шаг 3).

Что охраняется. В каталоге темы ``<корень>/.tide/topics/NN-slug/`` — журнал
решений ``decisions-tg.jsonl`` и ``facts.md``; в самом ``.tide/topics/`` —
``decisions-skip.jsonl`` (пары, которые сознательно не пишут). Это
«охраняемые файлы» (:func:`tracked_names`).

Журнал правок. Рядом с охраняемыми файлами — ``edits.jsonl``: каждая правка
пишет строку

    at       ISO с поясом — когда
    action   decide | skip | edit | migration | baseline
    session  claude-сессия ($CLAUDE_CODE_SESSION_ID или session_id хука) | null
    agent    agent_id сабагента (правка из сабагента) | null
    who      AO-сессия ($AO_SESSION_ID) | null
    tool     чем: cli | Edit | Write | MultiEdit | NotebookEdit | Bash
    files    какие охраняемые файлы изменились
    id       id записи решения | null
    src      ссылка на слово: tg:N | null
    note     пояснение | null
    before   {файл: sha256[:16] | null} — все охраняемые файлы каталога до правки
    after    то же после правки

Правило «без журнала правка не проходит»:

* перед записью tide (decide / skip / пропущенная хуком правка) сверяет sha
  файлов на диске с ``after`` последней строки (:func:`guard`); не сходится —
  запись отбита, пока расхождение не признано ``tide truth baseline --topic …
  --reason …``;
* ``tide truth check`` (:func:`check_dir`) сверяет цепочку: ``before`` каждой
  строки = ``after`` предыдущей (кроме ``baseline`` — она начинает цепочку
  заново), ``after`` последней = файлы на диске. Расхождение — ошибка «правка
  без журнала».

Стартовая строка: ``tide truth baseline [--project P]`` пишет ``baseline`` во
все каталоги без журнала правок. Каталог, где журнал есть, но файлы разошлись с
ним, baseline трогает только по имени (``--topic``) и с ``--reason``.

Сам ``edits.jsonl`` правит только tide: хук отбивает прямую правку у всех.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

EDITS_FILE = "edits.jsonl"
JOURNAL_FILE = "decisions-tg.jsonl"
FACTS_FILE = "facts.md"
SKIP_FILE = "decisions-skip.jsonl"
TOPICS_DIRNAME = "topics"

TOPIC_FILES = (JOURNAL_FILE, FACTS_FILE)
TOPICS_ROOT_FILES = (SKIP_FILE,)
ACTIONS = ("decide", "skip", "edit", "migration", "baseline")
SHA_LEN = 16


class EditsError(Exception):
    """Правка отбита: файлы разошлись с журналом правок."""


# --- где что ----------------------------------------------------------------

def is_topics_root(d: Path) -> bool:
    """*d* — сам ``.tide/topics`` (там лежит decisions-skip.jsonl)."""
    d = Path(d)
    return d.name == TOPICS_DIRNAME and d.parent.name == ".tide"


def is_topic_dir(d: Path) -> bool:
    """*d* — каталог темы ``.tide/topics/<slug>``."""
    return is_topics_root(Path(d).parent)


def tracked_names(d: Path) -> tuple:
    """Охраняемые файлы каталога *d* (пусто — каталог не охраняется)."""
    if is_topics_root(d):
        return TOPICS_ROOT_FILES
    if is_topic_dir(d):
        return TOPIC_FILES
    return ()


def protected(path: Path) -> Optional[str]:
    """Что за охраняемый файл *path*: ``record`` (журнал/факты/пропуски),
    ``edits`` (сам журнал правок) или None — не охраняется."""
    p = Path(path)
    if p.name == EDITS_FILE and (is_topics_root(p.parent) or is_topic_dir(p.parent)):
        return "edits"
    if p.name in tracked_names(p.parent):
        return "record"
    return None


# --- sha и журнал -----------------------------------------------------------

def sha(path: Path) -> Optional[str]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:SHA_LEN]


def state(d: Path) -> Dict[str, Optional[str]]:
    """``{файл: sha | None}`` охраняемых файлов каталога *d*."""
    return {n: sha(Path(d) / n) for n in tracked_names(d)}


def read_rows(d: Path) -> List[Dict]:
    """Строки журнала правок *d*; битые — объектом ``{"_bad": номер}``."""
    p = Path(d) / EDITS_FILE
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[Dict] = []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            r = None
        out.append(r if isinstance(r, dict) else {"_bad": i})
    return out


def last_after(d: Path) -> Optional[Dict[str, Optional[str]]]:
    """``after`` последней целой строки журнала, None — журнала нет."""
    for r in reversed(read_rows(d)):
        if isinstance(r.get("after"), dict):
            return r["after"]
    return None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def caller(session: Optional[str] = None, agent: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Кто правит: claude-сессия, сабагент, AO-сессия (из окружения, если не дали)."""
    sid = session if session is not None else (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    who = (os.environ.get("AO_SESSION_ID") or "").strip()
    return {"session": sid.strip() or None, "agent": (agent or "").strip() or None,
            "who": who or None}


def append(d: Path, action: str, before: Dict, after: Dict, *, tool: str = "cli",
           rid: Optional[str] = None, src: Optional[str] = None,
           note: Optional[str] = None, session: Optional[str] = None,
           agent: Optional[str] = None) -> Dict:
    """Дописать строку в журнал правок *d*; вернуть её."""
    if action not in ACTIONS:
        raise ValueError("edits: action {0!r} не из {1}".format(action, ACTIONS))
    row = {"at": _now(), "action": action, **caller(session, agent), "tool": tool,
           "files": sorted(n for n in after if before.get(n) != after.get(n)),
           "id": rid, "src": src, "note": note, "before": before, "after": after}
    p = Path(d) / EDITS_FILE
    prefix = ""
    if p.is_file():
        cur = p.read_text(encoding="utf-8")
        if cur and not cur.endswith("\n"):
            prefix = "\n"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(prefix + json.dumps(row, ensure_ascii=False) + "\n")
    return row


# --- сверка -----------------------------------------------------------------

def mismatch(d: Path) -> List[str]:
    """Чем файлы *d* на диске расходятся с журналом правок (пусто — сходятся).

    Журнала нет: сходится, только если охраняемых файлов тоже нет (новая тема).
    """
    cur = state(d)
    want = last_after(d)
    if want is None:
        have = [n for n, s in cur.items() if s is not None]
        if have:
            return ["нет журнала правок {0} при файлах {1} — заведи стартовую строку: "
                    "tide truth baseline".format(Path(d) / EDITS_FILE, ", ".join(have))]
        return []
    out = []
    for n, s in cur.items():
        if want.get(n) != s:
            out.append("{0}: правка без журнала — на диске sha {1}, в журнале правок {2}".format(
                Path(d) / n, s or "нет файла", want.get(n) or "нет файла"))
    return out


@contextmanager
def guard(d: Path, action: str, **kw) -> Iterator[None]:
    """Обёртка записи tide: до — файлы сходятся с журналом, после — строка журнала.

    Расхождение до записи — :class:`EditsError`, тело не выполняется. Строка
    пишется, только если тело дошло до конца и хоть один файл изменился.
    """
    d = Path(d)
    bad = mismatch(d)
    if bad:
        raise EditsError("без журнала правка не проходит:\n  " + "\n  ".join(bad)
                         + "\n  признать как есть: tide truth baseline --topic <проект>/<slug> "
                           "--reason \"…\"")
    before = state(d)
    yield
    after = state(d)
    if after != before:
        append(d, action, before, after, **kw)


def check_dir(d: Path) -> List[str]:
    """Ошибки журнала правок каталога *d*: цепочка и сверка с диском."""
    d = Path(d)
    rows = read_rows(d)
    errs: List[str] = []
    p = d / EDITS_FILE
    prev = None
    for i, r in enumerate(rows, 1):
        if "_bad" in r:
            errs.append("{0}:{1}: строка журнала правок не JSON-объект".format(p, r["_bad"]))
            continue
        before, after = r.get("before"), r.get("after")
        if not isinstance(after, dict) or not isinstance(before, dict):
            errs.append("{0}:{1}: нет before/after".format(p, i))
            continue
        if prev is not None and r.get("action") != "baseline" and before != prev:
            diff = sorted(n for n in set(before) | set(prev) if before.get(n) != prev.get(n))
            errs.append("{0}:{1}: правка без журнала между строками — {2} изменился "
                        "до этой правки".format(p, i, ", ".join(diff)))
        prev = after
    return errs + mismatch(d)


def baseline(d: Path, *, reason: Optional[str] = None, force: bool = False) -> Optional[Dict]:
    """Стартовая строка журнала правок *d*.

    Нет журнала — пишет (если есть хоть один файл). Журнал есть и сходится —
    ничего. Журнал есть и разошёлся — только с *force* и *reason*, иначе
    :class:`EditsError`.
    """
    d = Path(d)
    cur = state(d)
    want = last_after(d)
    if want is None:
        if all(s is None for s in cur.values()):
            return None
        return append(d, "baseline", {n: None for n in cur}, cur,
                      note=reason or "стартовая строка: файлы как были до журнала правок")
    if want == cur:
        return None
    if not (force and reason):
        raise EditsError("{0}: файлы разошлись с журналом правок — признать: "
                         "tide truth baseline --topic … --reason \"почему\"".format(d))
    return append(d, "baseline", dict(want), cur, note=reason)

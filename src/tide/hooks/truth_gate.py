"""tide.hooks.truth_gate — записи решений и фактов правит только держатель нити хранителя.

``tide hook truth-gate`` вешается на PreToolUse и PostToolUse (матчер
``Write|Edit|MultiEdit|NotebookEdit|Bash``) — SOC-130, шаг 3.

Охраняемые файлы (:func:`tide.truth_edits.protected`): ``decisions-tg.jsonl`` и
``facts.md`` в ``.tide/topics/<тема>/``, ``decisions-skip.jsonl`` в
``.tide/topics/``; ``edits.jsonl`` рядом с ними — журнал правок, его руками не
правит никто.

Кто проходит. Нить хранителя — ``context-keeper`` (``$TIDE_TRUTH_KEEPER``
переопределяет: ``<нить>`` или ``<проект>/<нить>``), ищется по ростеру. Держатель
— новейшая принятая сессия нити (:func:`tide.arc.holder.holder`, как ``tide
whois``). Проходит вызов, чей ``session_id`` = claude-сессия держателя: и сама
голова, и её сабагенты — у сабагента тот же ``session_id`` плюс ``agent_id``
(это её руки). Остальным — отказ (код 2) с адресом держателя для ``ao send``.
Нити хранителя нет нигде в ростере — хук молчит (механизм не заведён).

Что видит в Bash. Пути охраняемых файлов в тексте команды (относительные — от
``cwd`` из payload). Команда пишет, если в ней перенаправление ``>``, ``sed -i``
/ ``perl -i`` или звено не из списка чтения (:data:`_READ_ONLY`). Плюс вербы
записи ``tide truth decide|skip|baseline`` без ``--dry-run`` — тоже только
держателю (они сами пишут журнал правок). Скрипт, который пишет файл, не назвав
его, хук не увидит — это ловит ``tide truth check`` по журналу правок.

Журнал. Держателю PreToolUse ещё сверяет файлы каталога с журналом правок: уже
разошлись (правка мимо журнала) — отказ и ему, «без журнала правка не
проходит». PostToolUse после пропущенной правки пишет строку ``edit`` в
``edits.jsonl`` каталога (``before`` — последняя строка журнала, ``after`` —
диск), если файлы изменились.

Битый payload — пропуск (хук не запирает сессию на ошибке разбора), как у
соседних гейтов.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import truth_edits as edits

ALLOW = 0
BLOCK = 2

KEEPER_ENV = "TIDE_TRUTH_KEEPER"
KEEPER_THREAD = "context-keeper"

_EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}

_NAMES = (edits.JOURNAL_FILE, edits.FACTS_FILE, edits.SKIP_FILE, edits.EDITS_FILE)
# путь, кончающийся охраняемым именем: всё, что не пробел, кавычка и не синтаксис шелла
_PATH_RE = re.compile(r"[^\s'\"`;|&<>()=]*(?:" + "|".join(re.escape(n) for n in _NAMES) + r")")

# звенья, которые только читают (первое слово звена)
_READ_ONLY = frozenset({
    "cat", "head", "tail", "grep", "rg", "wc", "ls", "stat", "file", "diff", "sort",
    "uniq", "echo", "jq", "less", "more", "shasum", "sha256sum", "md5", "md5sum", "cd",
    "pwd", "find", "tree", "git", "sed", "cut", "nl", "column", "printf", "test", "[",
    "tide", "true",
})
_TRUTH_WRITE_VERBS = ("decide", "skip", "baseline")


# --- держатель ----------------------------------------------------------------

def keeper(home: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """Держатель нити хранителя ``{thread, project, claude_session, ao_session, title}``
    или None — такой нити нет ни в одном проекте ростера."""
    from .. import fields, paths, roster
    from ..arc import holder as _holder
    from ..arc.work import thread_dir

    ref = (os.environ.get(KEEPER_ENV) or "").strip() or KEEPER_THREAD
    want_project, _, name = ref.rpartition("/")
    if home is None:
        try:
            home = paths.control_home()
        except Exception:  # noqa: BLE001
            return None
    try:
        entries = roster.read_roster(home)
    except Exception:  # noqa: BLE001
        return None
    for e in entries:
        if want_project and e.get("name") != want_project:
            continue
        root = Path(e.get("path", "")).expanduser()
        if not root.is_dir() or thread_dir(root, name) is None:
            continue
        try:
            rec = _holder.holder(root, name, home=home)
        except Exception:  # noqa: BLE001
            continue
        title = ""
        if rec.get("arc"):
            pp = paths.arcs_dir(root) / str(rec["thread"]) / "arcs" / \
                str(rec["arc"]).split("/", 1)[1] / "arc.md"
            title = (fields.read_field(pp, "title") or "").strip()
        return {"thread": str(rec.get("thread") or name), "project": str(e.get("name")),
                "claude_session": str(rec.get("claude_session") or ""),
                "ao_session": str(rec.get("ao_session") or ""), "title": title}
    return None


def deny_message(k: Dict[str, str], what: str) -> str:
    ao = k.get("ao_session") or "<сессия держателя: tide whois {0}>".format(KEEPER_THREAD)
    who = k.get("title") or "{0}/{1}".format(k.get("project"), k.get("thread"))
    return ("tide: записи решений и фактов ведёт context-keeper — {0}. "
            "Сама не правь ({1}): пришли ей слово человека через "
            "ao send {2} \"<что сказал человек, tg:N, тема>\" — она запишет через "
            "tide truth decide.".format(who, what, ao))


# --- что трогает вызов ----------------------------------------------------------

def _resolve(raw: str, cwd: Optional[str]) -> Path:
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return Path(os.path.normpath(str(p)))


def bash_targets(command: str, cwd: Optional[str]) -> Tuple[List[Path], bool, bool]:
    """Bash → ``(охраняемые пути в тексте, пишет ли команда, есть ли верб записи tide truth)``."""
    from .role_gate import _STDERR_REDIRECT_RE, _split_segments, _unquoted

    found: List[Path] = []
    for m in _PATH_RE.finditer(command or ""):
        p = _resolve(m.group(0), cwd)
        if edits.protected(p) and p not in found:
            found.append(p)
    segments = _split_segments(command or "") or [command or ""]
    writes, truth_verb = False, False
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        bare = _unquoted(seg, expand_double=False)
        if ">" in _STDERR_REDIRECT_RE.sub(" ", bare):
            writes = True
        try:
            parts = shlex.split(seg)
        except ValueError:
            parts = seg.split()
        if not parts:
            continue
        head = os.path.basename(parts[0])
        if head == "tide" and len(parts) >= 3 and parts[1] == "truth" \
                and parts[2] in _TRUTH_WRITE_VERBS and "--dry-run" not in parts:
            truth_verb = True
        if head in ("sed", "perl") and any(x == "-i" or x.startswith("-i") or x == "--in-place"
                                           for x in parts[1:]):
            writes = True
        elif head not in _READ_ONLY:
            writes = True
    return found, writes, truth_verb


def targets(tool: str, tool_input: dict, cwd: Optional[str]) -> Tuple[List[Path], bool]:
    """``(охраняемые пути, которые вызов пишет, нужен ли держатель вообще)``."""
    if tool in _EDIT_TOOLS:
        raw = tool_input.get(_EDIT_TOOLS[tool]) or ""
        if not isinstance(raw, str) or not raw:
            return [], False
        p = _resolve(raw, cwd)
        return ([p], True) if edits.protected(p) else ([], False)
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if not isinstance(cmd, str):
            return [], False
        found, writes, verb = bash_targets(cmd, cwd)
        paths = found if writes else []
        return paths, bool(paths) or verb
    return [], False


# --- решение ----------------------------------------------------------------------

def decide(payload: dict, k: Optional[Dict[str, str]] = None, *,
           lookup=None) -> Tuple[bool, str]:
    """PreToolUse → ``(пропустить, причина отказа)``."""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    found, gated = targets(tool, ti, payload.get("cwd"))
    if not gated:
        return True, ""
    if any(edits.protected(p) == "edits" for p in found):
        return False, ("tide: {0} — журнал правок, его пишет только tide "
                       "(decide/skip/baseline и этот хук); руками не правится никем."
                       .format(next(p for p in found if edits.protected(p) == "edits")))
    k = k if k is not None else (lookup or keeper)()
    if k is None:
        return True, ""  # нити хранителя нет — механизм не заведён
    sid = str(payload.get("session_id") or "").strip()
    what = ", ".join(str(p) for p in found) or "tide truth decide/skip/baseline"
    if not sid or sid != k.get("claude_session"):
        return False, deny_message(k, what)
    for d in sorted({p.parent for p in found}):
        bad = edits.mismatch(d)
        if bad:
            return False, ("tide: без журнала правок правка не проходит — файлы уже "
                           "разошлись с {0}:\n  {1}\n  признать как есть: tide truth "
                           "baseline --topic <проект>/<slug> --reason \"…\"".format(
                               d / edits.EDITS_FILE, "\n  ".join(bad)))
    return True, ""


def record(payload: dict) -> List[Dict]:
    """PostToolUse: строка ``edit`` в журнал правок каждого изменившегося каталога."""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    found, _ = targets(tool, ti, payload.get("cwd"))
    rows = []
    for d in sorted({p.parent for p in found if edits.protected(p) == "record"}):
        before = edits.last_after(d)
        after = edits.state(d)
        if before is None:
            before = {n: None for n in after}
        if before == after:
            continue
        note = None
        if tool == "Bash":
            note = "bash: " + " ".join(str(ti.get("command") or "").split())[:200]
        rows.append(edits.append(d, "edit", before, after, tool=tool, note=note,
                                 session=str(payload.get("session_id") or ""),
                                 agent=str(payload.get("agent_id") or "")))
    return rows


def _read_payload(stream) -> dict:
    try:
        raw = stream.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_truth_gate(args) -> int:
    payload = _read_payload(sys.stdin)
    if not payload:
        return ALLOW
    event = payload.get("hook_event_name") or "PreToolUse"
    try:
        if event == "PostToolUse":
            record(payload)
            return ALLOW
        allow, reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 — сбой хука не запирает сессию
        print("tide: truth-gate сбой ({0}) — пропускаю".format(exc), file=sys.stderr)
        return ALLOW
    if not allow:
        print(reason, file=sys.stderr)
        return BLOCK
    return ALLOW

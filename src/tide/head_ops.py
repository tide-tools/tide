"""tide.head_ops — быстрые команды головы менеджера: ``tide ok`` и ``tide task`` (SOC-271).

Зачем: дать ок роли стоило 4 хода (``tide mail read`` → угадать синтаксис
``ao send``), а слово пользователя → задача — 5 ходов (create → Todo →
«Назначено» → ``ao send`` → offload), и обратные кавычки в теле ломали role-gate.
Одна команда — один ход.

* ``tide ok <N> ["текст"]`` — ответ на письмо N почты СВОЕЙ нити: письмо в нить
  отправителя (:func:`tide.mail.send`, пинг — сигнал «почта NNN … tide mail read
  <нить> NNN» его ведущей сессии, сразу, не пачкой), затем квитанция ``ack`` на
  письмо N и событие ``replied`` в журнал ящика. Без текста — «ок».
* ``tide task "заголовок" --to <проект>/<нить> [--parent SOC-N] [--body … |
  --body-file …] [--state Todo] [--project …]`` — задача через linear-as-manager
  (тело — stdin, без шелла: обратные кавычки целы), статус, комментарий
  «Назначено: <проект> — <нить> (<AO id>)», сигнал ``ao send`` ведущей сессии
  нити и строка в пульс своей сессии. ``--dry-run`` — план без записи.

Путь к linear-as-manager — тот же, что пускает role-gate
(:data:`tide.hooks.role_gate._LINEAR_AS_MANAGER`); ``$TIDE_LINEAR_AS_MANAGER``
подменяет его (тесты, чужая машина).
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import mail, paths, slug
from .arc.stream import StreamError

LAM_ENV = "TIDE_LINEAR_AS_MANAGER"
DEFAULT_OK = "ок"
DEFAULT_STATE = "Todo"
REPLIED_EV = "replied"
_ISSUE = re.compile(r"\b([A-Z]+-\d+)\b\s+(\S+)")
_AO_IN_PARENS = re.compile(r"\(([^()\s]+-\d+)\)")

BODY_TEMPLATE = (
    "**Зачем:** (заполнить: 1–2 строки — что изменится для пользователя)\n\n"
    "**Результат:** (заполнить: что будет видно или готово)\n\n"
    "**Как проверить:** (заполнить: где посмотреть пруф)\n\n"
    "**Ссылки:** {links}\n"
)


class OpsError(StreamError):
    """Быстрая команда не сложилась."""


# --- общее -----------------------------------------------------------------------

def lam_path() -> str:
    env = (os.environ.get(LAM_ENV) or "").strip()
    if env:
        return env
    from .hooks.role_gate import _LINEAR_AS_MANAGER

    return _LINEAR_AS_MANAGER


def _home() -> Optional[Path]:
    try:
        return paths.control_home()
    except Exception:  # noqa: BLE001
        return None


def _project_roots(prefer: str = "") -> List[Path]:
    """Корни проектов ростера; проект *prefer* — первым."""
    home = _home()
    out: List[Path] = []
    try:
        from . import roster

        rows = roster.read_roster(home) if home is not None else []
    except Exception:  # noqa: BLE001
        rows = []
    rows = sorted(rows, key=lambda e: 0 if e.get("name") == prefer else 1)
    for e in rows:
        p = Path(e["path"]).expanduser()
        if p not in out:
            out.append(p)
    return out


def _thread_dirs(root: Path) -> List[Path]:
    base = paths.arcs_dir(Path(root))
    if not base.is_dir():
        return []
    return [d for d in sorted(base.iterdir())
            if d.is_dir() and slug.is_entry(d.name) and not slug.is_closed_entry(d.name)]


def _ao_candidates(sender: str) -> List[str]:
    s = (sender or "").strip()
    out = [s] if s else []
    out += _AO_IN_PARENS.findall(s)
    return [c for c in dict.fromkeys(out) if re.search(r"-\d+", c)]


def thread_of_ao(ao: str) -> Optional[Path]:
    """Нить, где AO-сессия *ao* (или uds-имя «ao-xx», или подпись «… (ao)») —
    открытая сессия. Сначала проект по префиксу AO-id; не передавшая — первой."""
    cands = _ao_candidates(ao)
    if not cands:
        return None
    prefer = cands[-1].rsplit("-", 1)[0]
    found: Optional[Path] = None
    for root in _project_roots(prefer):
        for tdir in _thread_dirs(root):
            try:
                rows = mail.thread_sessions(tdir)
            except Exception:  # noqa: BLE001
                continue
            for r in rows:
                a = str(r.get("ao") or "")
                if a and any(c == a or c.startswith(a + "-") for c in cands):
                    if not r.get("handed"):
                        return tdir
                    found = found or tdir
    return found


def sender_thread(sender: str) -> Optional[Path]:
    """Нить отправителя письма: AO-id → нить; «нить·арка» → нить текущего проекта."""
    s = (sender or "").strip()
    if not s or s == "человек":
        return None
    if "·" in s:
        root = paths.find_tide_root()
        if root is not None:
            try:
                return mail.thread_dir(s.split("·", 1)[0], root)
            except StreamError:
                pass
    return thread_of_ao(s)


def own_session() -> Optional[Path]:
    """Арка-сессия вызывающего: claude-id в текущем проекте, иначе по AO-id."""
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    ao = (os.environ.get(mail.AO_SESSION_ENV) or "").strip()
    from . import offload

    roots: List[Path] = []
    here = paths.find_tide_root()
    if here is not None:
        roots.append(here)
    if sid:
        for root in roots + _project_roots():
            try:
                entry = offload.find_session_by_claude_id(root, sid)
            except Exception:  # noqa: BLE001
                entry = None
            if entry is not None:
                return entry
    if ao:
        tdir = thread_of_ao(ao)
        if tdir is not None:
            for r in mail.thread_sessions(tdir):
                a = str(r.get("ao") or "")
                if a and (ao == a or ao.startswith(a + "-")):
                    return Path(r["entry"])
    return None


def own_thread(ref: str = "") -> Path:
    if ref:
        return mail.target(ref)[0]
    entry = own_session()
    if entry is None:
        raise OpsError("ok: не нашёл свою нить (нет $CLAUDE_CODE_SESSION_ID/$AO_SESSION_ID "
                       "в открытой сессии) — укажи --thread <проект>/<нить>")
    return entry.parents[1]


def lead_targets(tdir: Path) -> List[str]:
    """Кому сигнал в нить: живые сессии, когда их несколько, иначе ведущая
    (как ``tide whois <нить>``) — тот же выбор, что у пинга почты."""
    try:
        live = mail.live_sessions(tdir)
    except Exception:  # noqa: BLE001
        live = []
    if len(live) > 1:
        return list(dict.fromkeys(str(r["ao"]) for r in live))
    ao = mail._holder_ao(tdir)
    return [ao] if ao else []


def _send_signal(to: str, text: str) -> Optional[int]:
    from .launcher.autohandoff import _ao_send

    return _ao_send(to, text)


def _pulse(note: str) -> str:
    """Строка в пульс своей сессии — ядро offload без обёрток (шов-синк не будим)."""
    entry = own_session()
    if entry is None:
        return "пульс пропущен: своя сессия не нашлась"
    from . import offload

    try:
        offload.write_pulse(entry, note=note)
    except Exception as exc:  # noqa: BLE001
        return "пульс пропущен: {0}".format(exc)
    return "пульс → {0}".format(entry.name)


# --- tide ok ---------------------------------------------------------------------

def ok_text(rec: Dict[str, object], text: str) -> str:
    first = str(rec.get("first") or "").strip()
    head = "{0} — ответ на письмо {1}".format((text or "").strip() or DEFAULT_OK, rec["num"])
    if first:
        head += " («{0}»)".format(first[:80])
    return head


def ok(num: object, text: str = "", *, thread: str = "", to: str = "",
       dry_run: bool = False) -> Dict[str, object]:
    """Ответить на письмо *num* своей нити. → ``{num, sender, to, reply, acked}``."""
    tdir = own_thread(thread)
    rec = mail.letter(tdir, num)
    sender = str(rec.get("sender") or "")
    dest = mail.target(to)[0] if to else sender_thread(sender)
    body = ok_text(rec, text)
    out: Dict[str, object] = {"num": rec["num"], "sender": sender, "box": mail.address_of(tdir),
                              "to": mail.address_of(dest) if dest is not None else None,
                              "text": body, "reply": None, "acked": False}
    if dry_run:
        out["targets"] = lead_targets(dest) if dest is not None else []
        return out
    if dest is None:
        raise OpsError("ok: отправитель письма {0} — {1!r}, его нить не нашлась; "
                       "ответ не ушёл — укажи --to <проект>/<нить>".format(rec["num"], sender))
    sent = mail.send(dest.name, body, root=dest.parents[2], urgent=True)
    out["reply"] = sent
    mail.read(tdir.name, rec["num"], root=tdir.parents[2])
    mail._append(tdir, {"ev": REPLIED_EV, "num": rec["num"],
                        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": out["to"], "reply": sent["num"]})
    out["acked"] = True
    return out


def _cmd_ok(args) -> int:
    out = ok(args.num, " ".join(args.text or []), thread=args.thread or "",
             to=args.to or "", dry_run=args.dry_run)
    if args.dry_run:
        print("план (--dry-run): письмо {0} от {1} в {2} → ответ в {3}: «{4}»; сигнал {5}; "
              "квитанция + replied в журнал".format(
                  out["num"], out["sender"], out["box"], out["to"] or "— нить не нашлась",
                  out["text"], ", ".join(out.get("targets") or []) or "— ведущей нет"))
        return 0
    sent = out["reply"] or {}
    p = sent.get("ping") or {}
    how = "сигнал {0} → код {1}".format(p.get("to"), p.get("code")) if p.get("to") \
        else "сигнал не ушёл: у нити нет ведущей AO-сессии"
    print("ок: ответ на {0} ({1}) → письмо {2} в {3} · {4} · {0} прочитано и отвечено".format(
        out["num"], out["sender"], sent.get("num"), out["to"], how))
    return 0


# --- tide task -------------------------------------------------------------------

def _lam(args: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run([lam_path()] + args, input=stdin, capture_output=True,
                              text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def task_body(body: str, parent: str) -> str:
    if (body or "").strip():
        return body if body.endswith("\n") else body + "\n"
    return BODY_TEMPLATE.format(links=parent or "(заполнить: SOC-N, документы, абсолютные пути, repo@sha)")


def task(title: str, to: str, *, parent: str = "", body: str = "", state: str = DEFAULT_STATE,
         project: str = "", dry_run: bool = False) -> Dict[str, object]:
    """Задача целиком; → ``{ident, url, steps: [(шаг, ok, вывод)], …}``."""
    title = (title or "").strip()
    if not title:
        raise OpsError("task: пустой заголовок")
    tdir = mail.target(to)[0]
    addr = mail.address_of(tdir)
    proj, _sep, name = addr.rpartition("/")
    proj = proj or "?"
    targets = lead_targets(tdir)
    ao_txt = ", ".join(targets) or "ведущей сессии нет"
    text = task_body(body, parent)
    create = ["create", "--title", title, "--body", "-"]
    if parent:
        create += ["--parent", parent]
    if project:
        create += ["--project", project]
    assigned = "Назначено: {0} — {1} ({2})".format(proj, name, ao_txt)
    out: Dict[str, object] = {"title": title, "to": addr, "targets": targets, "ident": None,
                              "url": None, "steps": [], "body": text, "assigned": assigned,
                              "create": create, "state": state}
    if dry_run:
        return out
    steps: List[Tuple[str, bool, str]] = []
    rc, so, se = _lam(create, stdin=text)
    m = _ISSUE.search(so) if rc == 0 else None
    if not m:
        raise OpsError("task: linear-as-manager create не дал SOC-N (код {0}): {1}".format(
            rc, (se or so)[:300]))
    ident, url = m.group(1), m.group(2)
    out.update(ident=ident, url=url)
    steps.append(("create", True, so))
    if state and state.strip() not in ("-", "none"):
        rc, so, se = _lam(["state", ident, state])
        steps.append(("state", rc == 0, so or se))
    rc, so, se = _lam(["comment", ident, "-"], stdin=assigned)
    steps.append(("comment", rc == 0, so or se))
    signal = "задача {0}: {1} — {2}".format(ident, title, url)
    for t in targets:
        code = _send_signal(t, signal)
        steps.append(("ao send " + t, code == 0, "код {0}".format(code)))
    if not targets:
        steps.append(("ao send", False, "у нити {0} нет ведущей AO-сессии".format(addr)))
    steps.append(("пульс", True, _pulse("tide task: {0} «{1}» → {2} ({3})".format(
        ident, title, addr, ao_txt))))
    out["steps"] = steps
    out["signal"] = signal
    return out


def _cmd_task(args) -> int:
    body = args.body or ""
    if args.body_file:
        body = Path(args.body_file).expanduser().read_text(encoding="utf-8")
    out = task(args.title, args.to, parent=args.parent or "", body=body,
               state=args.state, project=args.project or "", dry_run=args.dry_run)
    if args.dry_run:
        print("план (--dry-run), ничего не пишу:")
        print("  1. {0} {1}  (тело {2} Б — stdin)".format(
            lam_path(), " ".join(repr(a) if " " in a else a for a in out["create"]),
            len(str(out["body"]).encode("utf-8"))))
        if out["state"] and str(out["state"]).strip() not in ("-", "none"):
            print("  2. state SOC-N {0}".format(out["state"]))
        print("  3. comment SOC-N «{0}»".format(out["assigned"]))
        print("  4. ao send {0}: «задача SOC-N: {1} — <url>»".format(
            ", ".join(out["targets"]) or "— ведущей нет", out["title"]))
        print("  5. пульс своей сессии")
        print("--- тело ---")
        print(str(out["body"]).rstrip())
        return 0
    print("{0} {1}".format(out["ident"], out["url"]))
    bad = 0
    for step, good, msg in out["steps"]:  # type: ignore[union-attr]
        bad += 0 if good else 1
        print("  {0} {1}: {2}".format("ok" if good else "FAIL", step, msg))
    return 1 if bad else 0


# --- регистрация -----------------------------------------------------------------

def register(subparsers) -> None:
    o = subparsers.add_parser("ok", help="reply to letter N of your thread's mail: letter to "
                                         "the sender's thread + signal, mark read (SOC-271)")
    o.add_argument("num", help="letter number in your thread's inbox")
    o.add_argument("text", nargs="*", help="reply text (default: «ок»)")
    o.add_argument("--thread", help="your thread, if not found from the session "
                                    "(<проект>/<нить>)")
    o.add_argument("--to", help="reply to this thread instead of the sender's")
    o.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    o.set_defaults(func=_cmd_ok, _cmd="ok")

    t = subparsers.add_parser("task", help="Linear task in one go: create → state → "
                                           "«Назначено» → signal → pulse (SOC-271)")
    t.add_argument("title")
    t.add_argument("--to", required=True, help="<проект>/<нить> — исполнитель")
    t.add_argument("--parent", help="parent issue SOC-N")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--body", help="description markdown (default: template "
                                  "Зачем/Результат/Как проверить/Ссылки)")
    g.add_argument("--body-file", help="read the description from a file")
    t.add_argument("--state", default=DEFAULT_STATE,
                   help="state after create (default Todo; '-' — leave Backlog)")
    t.add_argument("--project", help="Linear project name")
    t.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    t.set_defaults(func=_cmd_task, _cmd="task")


__all__ = ["BODY_TEMPLATE", "OpsError", "lam_path", "lead_targets", "ok", "ok_text",
           "own_session", "own_thread", "register", "sender_thread", "task", "task_body",
           "thread_of_ao"]

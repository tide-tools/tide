"""SOC-271: ``tide say "текст"`` — сразу написать пользователю в личку Telegram.

Тонкая обёртка над ``tg-alert`` пульта (:mod:`tide.tg_pult`): маршрут ``human`` без темы
и без группы — то есть личка, не «Вопросы» доски и не тема форума. Текст уходит в tg-alert
через stdin (никакой текст не примут за флаг). ``TIDE_ALERT_SOURCE`` ставится из своей
сессии вызывающего (:func:`tide.tg_pult.caller_source`).

tg-alert только кладёт запись в outbox; демон пульта отправляет её за ~15 с и пишет в
``dialog.jsonl`` строку ``kind: alert`` с тем же ``qid`` и ``tg_msg_ids``. По умолчанию
``tide say`` ждёт эту строку до :data:`WAIT_SEC` секунд и печатает номер сообщения;
``--no-wait`` — не ждать.

    tide say "текст"                       # или: echo текст | tide say
    tide say --file /abs/pic.png "вот скрин"
    tide say --reply-to 4970 "ответ на его сообщение 4970"
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import tg_pult
from .tg_pult import PultError

WAIT_SEC = 40.0
POLL_SEC = 1.0
TAIL_BYTES = 512 * 1024


def build_cmd(*, files: List[str], reply_to: Optional[int], html: bool, dry: bool) -> List[str]:
    cmd = list(tg_pult.alert_cmd())
    for f in files:
        cmd += ["--file", str(Path(f).expanduser().resolve())]
    if reply_to:
        cmd += ["--reply-to", str(int(reply_to))]
    if html:
        cmd.append("--html")
    if dry:
        cmd.append("--dry")
    return cmd


def find_sent(qid: str, dialog: Path) -> Optional[Dict[str, object]]:
    """Строка ``dialog.jsonl`` пульта об отправке *qid* (``kind: alert``, ``dir: out``) или None."""
    try:
        with open(dialog, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if qid not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("qid") == qid and rec.get("dir") == "out":
            return rec
    return None


def say(text: str, *, files: Optional[List[str]] = None, reply_to: Optional[int] = None,
        html: bool = False, dry: bool = False, wait: float = WAIT_SEC,
        sleep: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    """Поставить сообщение в личку через tg-alert → ``{qid, tg, source, dialog, dry, out}``."""
    files = list(files or [])
    for f in files:
        if not Path(f).expanduser().is_file():
            raise PultError("say: --file {0}: нет такого файла".format(f))
    text = (text or "").strip()
    if not text and not files:
        raise PultError("say: пустой текст — нечего слать")
    source = tg_pult.caller_source()
    env = dict(os.environ, TIDE_ALERT_ROUTE="human", TIDE_ALERT_SOURCE=source)
    for k in ("TIDE_ALERT_TEXT", "TIDE_ALERT_URGENT"):
        env.pop(k, None)
    proc = tg_pult.run(build_cmd(files=files, reply_to=reply_to, html=html, dry=dry),
                       env=env, input_text=text, timeout=30)
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout or "").strip() or "код {0}".format(proc.returncode)
        raise PultError("say: tg-alert отказал: {0}".format(why))
    out = (proc.stdout or "").strip()
    res: Dict[str, object] = {"qid": None, "tg": None, "source": source, "dry": dry, "out": out,
                              "dialog": None}
    if dry:
        return res
    qid = next((ln.strip() for ln in reversed(out.splitlines()) if ln.strip().startswith("q-")), None)
    res["qid"] = qid
    try:
        dialog = tg_pult.dialog_file()
    except PultError:
        dialog = None
    res["dialog"] = str(dialog) if dialog else None
    if qid and dialog is not None and wait > 0:
        deadline = time.monotonic() + wait
        while True:
            rec = find_sent(qid, dialog)
            if rec is not None:
                ids = rec.get("tg_msg_ids") or ([rec["tg_msg_id"]] if rec.get("tg_msg_id") else [])
                res["tg"] = list(ids)
                res["sent_at"] = rec.get("ts")
                res["error"] = rec.get("error")
                break
            if time.monotonic() >= deadline:
                break
            sleep(POLL_SEC)
    return res


def render(res: Dict[str, object]) -> str:
    if res.get("dry"):
        return "say: --dry, ничего не поставлено\n" + str(res.get("out") or "")
    src = " · от {0}".format(res["source"]) if res.get("source") else ""
    if res.get("tg"):
        return "say: ушло в личку · tg {0} · {1}{2} · {3}".format(
            ",".join(str(i) for i in res["tg"]), res.get("qid"), src, res.get("dialog"))
    if res.get("error"):
        return "say: пульт не отправил ({0}) · {1}{2}".format(res["error"], res.get("qid"), src)
    return ("say: в очереди пульта · {0}{1} — демон шлёт за ~15 с, номер появится в {2}"
            .format(res.get("qid") or "?", src, res.get("dialog") or "dialog.jsonl"))


def run_cli(args) -> int:
    parts = list(getattr(args, "text", None) or [])
    text = " ".join(parts)
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read()
    try:
        res = say(text, files=getattr(args, "file", None) or [], reply_to=getattr(args, "reply_to", None),
                  html=bool(getattr(args, "html", False)), dry=bool(getattr(args, "dry", False)),
                  wait=0 if getattr(args, "no_wait", False) else WAIT_SEC)
    except PultError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(render(res))
    return 0


def register(sub) -> None:
    ap = sub.add_parser("say", help="сразу написать пользователю в личку Telegram (SOC-271, через tg-alert пульта)")
    ap.add_argument("text", nargs="*", help="текст (нет — читается stdin); лёгкий markdown, как у tg-alert")
    ap.add_argument("--file", action="append", default=[], metavar="PATH",
                    help="вложение (повторяемо): картинки — фото, прочее — документом")
    ap.add_argument("--reply-to", dest="reply_to", type=int, default=None, metavar="N",
                    help="ответить реплаем на его сообщение tg N")
    ap.add_argument("--html", action="store_true", help="текст — Telegram HTML (как tg-alert --html)")
    ap.add_argument("--dry", action="store_true", help="проверить и показать, ничего не ставить")
    ap.add_argument("--no-wait", dest="no_wait", action="store_true",
                    help="не ждать номер сообщения в журнале пульта")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.set_defaults(func=run_cli, _cmd="say")

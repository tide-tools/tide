"""SOC-143 F82 и F86 (acceptance-09 §9 пп. 3 и 7, пара 16 context-keeper 04→05).

F82 — F60 снимала «поручено мне», только если пульс называл отправителя AO-id.
Пульс «шов-синк 001/002 разобран: снято main · 05 …» и «шаг 2 сдан · 03» звали его
ролью — поручения оставались и через F56 размножались в хвосты. Теперь роль
сопоставляется с AO-id по реестру сессий (title «проект — нить · NN · <AO-id>»), а
почта шов-синка разобрана по квитанции прочтения + пульсу «беру / снимаю».

F86 — в одном блоке «ушёл ~21:55 UTC» рядом с местными 19:2x: сборщик переводит
UTC в местное время машины.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tide.launcher import seed_draft as sd


@pytest.fixture
def msk(monkeypatch):
    """Местное время −03, транскрипт и почта — UTC."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _passport(root: Path, thread: str, session: str, title: str) -> Path:
    d = root / ".tide" / "arcs" / thread / "arcs" / session
    d.mkdir(parents=True)
    (d / "arc.md").write_text("# {0}\n\ntitle: {1}\n".format(session, title),
                              encoding="utf-8")
    return d


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "tide-stack"
    _passport(root, "34-@context-keeper", "03-context-keeper-3",
              "tide-stack — context-keeper · 03 · tide-stack-22")
    old = _passport(root, "34-@context-keeper", "04-context-keeper-4",
                    "tide-stack — context-keeper · 04 · tide-stack-28")
    _passport(root, "31-@main", "05-main-5", "tide-stack — main · 05 · tide-stack-31")
    _passport(root, "30-@handoffs", "03-handoffs-3",
              "tide-stack — handoffs · 03 · tide-stack-21")
    return root, old


# --- реестр ролей ---------------------------------------------------------------

def test_project_roles_from_titles(project):
    root, _ = project
    roles = sd._project_roles(root)
    assert roles["tide-stack-31"] == "main · 05"
    assert roles["tide-stack-22"] == "context-keeper · 03"


def test_role_names_sender_other_thread(project):
    root, old = project
    r = sd.sender_roles(root, old)
    at = datetime(2026, 10, 9, 19, 20, 32)
    pulses = [(datetime(2026, 10, 9, 19, 21, 7),
               "- 2026-10-09T19:21:07 — курсор → шов-синк 001/002 разобран: снято "
               "main · 05 — шаг 3 (tide@a6b3648)")]
    assert not sd.pulse_names_sender("tide-stack-31", at, pulses)  # было (F82)
    assert sd.pulse_names_sender("tide-stack-31", at, pulses, r)


def test_short_role_own_thread(project):
    root, old = project
    r = sd.sender_roles(root, old)
    at = datetime(2026, 10, 9, 15, 23)
    pulses = [(datetime(2026, 10, 9, 15, 24, 30),
               "- 2026-10-09T15:24:30 — курсор → шаг 2 сдан · 03: код на диске, "
               "3000 passed")]
    assert sd.pulse_names_sender("tide-stack-22", at, pulses, r)


def test_short_role_of_other_thread_does_not_count(project):
    root, old = project
    r = sd.sender_roles(root, old)
    at = datetime(2026, 10, 9, 15, 23)
    pulses = [(datetime(2026, 10, 9, 15, 24), "- 2026-10-09T15:24:00 — коммит у "
               "handoffs · 03, всё")]
    assert not sd.pulse_names_sender("tide-stack-22", at, pulses, r)
    # чужая нить с тем же номером — не своя сессия 03; полная роль — да
    assert not sd.pulse_names_sender("tide-stack-31", at, pulses, r)


def test_role_pulse_before_delivery_does_not_close(project):
    root, old = project
    r = sd.sender_roles(root, old)
    pulses = [(datetime(2026, 10, 9, 19, 0), "- 2026-10-09T19:00:00 — снято main · 05")]
    assert not sd.pulse_names_sender("tide-stack-31", datetime(2026, 10, 9, 19, 20),
                                     pulses, r)


def test_role_with_wait_does_not_close(project):
    root, old = project
    r = sd.sender_roles(root, old)
    pulses = [(datetime(2026, 10, 9, 19, 21), "- 2026-10-09T19:21:00 — запросила у "
               "main · 05 разбор, жду к 20:00")]
    assert not sd.pulse_names_sender("tide-stack-31", datetime(2026, 10, 9, 19, 20),
                                     pulses, r)


# --- почта шов-синка ------------------------------------------------------------

def _mail_log(thread: Path, events):
    box = thread / "inbox"
    box.mkdir(parents=True, exist_ok=True)
    (box / "log.jsonl").write_text("\n".join(json.dumps(e) for e in events),
                                   encoding="utf-8")


BODY = ("почта 002 от шов-синк: шов-синк 001: поправка от tide-stack-31 — "
        "tide mail read tide-stack/context-keeper 002")


def test_mail_reads_by_me_local(project, msk):
    _, old = project
    _mail_log(old.parents[1], [
        {"ev": "sent", "num": "002", "at": "2026-10-09T22:20:32Z"},
        {"ev": "ack", "num": "002", "at": "2026-10-09T22:20:42Z", "by": "tide-stack-28"},
        {"ev": "ack", "num": "001", "at": "2026-10-09T22:20:16Z", "by": "tide-stack-31"},
    ])
    reads = sd.mail_reads(old.parents[1], "tide-stack-28")
    assert reads == {"002": datetime(2026, 10, 9, 19, 20, 42)}


def test_mail_closed_by_read_and_take_drop():
    at = datetime(2026, 10, 9, 19, 20, 32)
    reads = {"002": datetime(2026, 10, 9, 19, 20, 42)}
    pulses = [(datetime(2026, 10, 9, 19, 21, 7), "- 2026-10-09T19:21:07 — Беру: "
               "сверка 12.10. Снимаю: топик betbook — сделан сессией 03")]
    assert sd.closed_by_mail_read(BODY, at, pulses, reads)
    # без квитанции — не разобрано
    assert not sd.closed_by_mail_read(BODY, at, pulses, {})
    # квитанция есть, пульса «беру/снимаю» после неё нет — не разобрано
    assert not sd.closed_by_mail_read(BODY, at, [(datetime(2026, 10, 9, 19, 21),
                                                  "- прочла, думаю")], reads)
    assert not sd.closed_by_mail_read(BODY, at, [(datetime(2026, 10, 9, 19, 10),
                                                  "- снимаю старое")], reads)


def _delivery(ts: str, who: str, body: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": (
        "<ao-session-delivery id=\"session-send:{0}\">\n[from {1}] {2}\n"
        "</ao-session-delivery>").format(ts, who, body)}}


def test_incoming_orders_pair16(project, msk, tmp_path, monkeypatch):
    """Пара 16: обе строки «поручено мне» (S:140–141) больше не выходят."""
    root, old = project
    sid = "f82-life"
    d = tmp_path / "cfg" / "projects" / "-ao-tide-stack-28"
    d.mkdir(parents=True)
    rows = [
        _delivery("2026-10-09T18:23:00Z", "tide-stack-22",
                  "[context-keeper · 03 → 04] Итог шага 2. Готово, не закоммичено: "
                  "tide truth inbox и decide, 13 новых тестов, проверь и закоммить"),
        _delivery("2026-10-09T22:20:32Z", "tide-stack-31", BODY),
    ]
    t = d / "{0}.jsonl".format(sid)
    t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    ctx = [
        "- 2026-10-09T15:00:00 — нить принята",
        "- 2026-10-09T15:24:30 — курсор → шаг 2 сдан · 03: код на диске, 3000 passed",
        "- 2026-10-09T19:21:07 — курсор → шов-синк разобран. Беру: сверка 12.10.",
    ]
    since = datetime(2026, 10, 9, 14, 0)
    before = sd.incoming_orders(sd.transcript_path(sid), since, ctx)
    assert any("tide-stack-22" in ln for ln in before)  # дефект F82 воспроизведён
    _mail_log(old.parents[1], [
        {"ev": "ack", "num": "002", "at": "2026-10-09T22:20:42Z", "by": "tide-stack-28"}])
    after = sd.incoming_orders(sd.transcript_path(sid), since, ctx,
                               roles=sd.sender_roles(root, old),
                               mail_reads=sd.mail_reads(old.parents[1], "tide-stack-28"))
    assert after == []


# --- F86: местное время ---------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("ушёл ~21:55 UTC, management-20", "ушёл ~18:55, management-20"),
    ("сдал итог в 22:22:30Z", "сдал итог в 19:22:30"),
    ("ack 2026-10-09T22:20:42Z", "ack 2026-10-09T19:20:42"),
    ("(tg 3523, 09.10 20:36 UTC; facts.md)", "(tg 3523, 09.10 17:36; facts.md)"),
    ("09.10 01:30 UTC", "08.10 22:30"),
    ("- 2026-10-09T19:21:07 — курсор", "- 2026-10-09T19:21:07 — курсор"),
    ("местное 19:20, хеш 1c1c3d705e1b", "местное 19:20, хеш 1c1c3d705e1b"),
    ("/x/y/12:30Z.md", "/x/y/12:30Z.md"),
])
def test_local_times(msk, raw, want):
    assert sd.local_times(raw, now=datetime(2026, 10, 9, 19, 0)) == want


def test_seal_budgeted_local(msk):
    """Весь сид (и «где мы», и сырой журнал) — в местном времени."""
    ctx = ["- 2026-10-09T19:20:07 — курсор → ❓ про настройки ушёл ~21:55 UTC"]
    text = sd.seal_budgeted(lambda clip: {sd.WHERE: [ctx[0]]}, context=ctx)
    assert "UTC" not in text
    assert "~18:55" in text

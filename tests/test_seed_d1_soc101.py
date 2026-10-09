"""SOC-101 пакет Д1 — доделки сборщика сида по приёмке acceptance-03 (пара 10).

F28 доставки очередью · F29 окно входящих до ПЕРЕДАЧИ · F30 статусы из Linear ·
F31 сжатие журнала · F32 шапка · F33 неотвеченные · F34 курсор до ПЕРЕДАЧИ ·
C11 карта входа · C4 правила без «not done». Сеть не трогается.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tide.launcher import seed_draft


@pytest.fixture
def msk(monkeypatch):
    """Пульсы — местное время −03, транскрипт — UTC."""
    import time

    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _queued(ts: str, prompt: str) -> dict:
    return {"type": "attachment", "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": prompt,
                           "commandMode": "prompt"}}


def _user(ts: str, text: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


# --- F28: доставки очередью ------------------------------------------------------

def test_queued_deliveries_are_incoming_orders(tmp_path, msk):
    t = tmp_path / "t.jsonl"
    rows = [
        _queued("2026-10-09T11:25:30Z", "<ao-session-delivery id=\"s:1\">\n[from tide-stack-12] "
                "Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, pytest 2849\n</ao-session-delivery>"),
        _queued("2026-10-09T11:25:47Z", "<cross-session-message from=\"uds:x\" "
                "from-name=\"tide-stack-20-9e\">приёмка идёт</cross-session-message>"),
        _queued("2026-10-09T11:25:50Z", "<task-notification><task-id>a1</task-id>"
                "</task-notification>"),
        {"type": "queue-operation", "timestamp": "2026-10-09T11:25:30Z",
         "content": "<ao-session-delivery>[from tide-stack-12] Пакет Г СДАН</ao-session-delivery>"},
        # та же доставка ещё и репликой — один раз
        _user("2026-10-09T11:25:31Z", "<ao-session-delivery id=\"s:1\">\n[from tide-stack-12] "
              "Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, pytest 2849\n</ao-session-delivery>"),
        _queued("2026-10-09T11:25:55Z", "<ao-session-delivery>[from m-1] Your claude.ai usage "
                "limit has reset.</ao-session-delivery>"),
    ]
    t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = seed_draft.incoming_orders(t, datetime(2026, 10, 9, 8, 0))
    assert out == [
        "- поручено мне — от tide-stack-20-9e в 08:25: приёмка идёт",
        "- поручено мне — от tide-stack-12 в 08:25: Пакет Г СДАН: 230b37b F22 … 29cfac7 F27, "
        "pytest 2849",
    ]


# --- F29: окно от пульса перед ПЕРЕДАЧЕЙ -----------------------------------------

CTX = [
    "- 2026-10-09T08:10:00 — сдала промер",
    "- 2026-10-09T08:22:00 — остаток F23/F16, F27 у handoffs · 03",
    "- 2026-10-09T08:26:26 — ПЕРЕДАЧА. В полёте: коммит truth у handoffs · 03",
]


def test_window_starts_at_the_pulse_before_the_handover():
    assert seed_draft.incoming_since(CTX) == datetime(2026, 10, 9, 8, 22)
    # ПЕРЕДАЧА не последняя — окно всё равно от пульса перед ней
    assert seed_draft.incoming_since(CTX + ["- 2026-10-09T08:27:00 — шлю номер"]) == \
        datetime(2026, 10, 9, 8, 22)
    # ПЕРЕДАЧИ нет — от предпоследнего пульса
    assert seed_draft.incoming_since(CTX[:2]) == datetime(2026, 10, 9, 8, 10)
    assert seed_draft.incoming_since(CTX[:1]) is None


def test_report_a_minute_before_handover_reaches_the_seed(tmp_path, msk, monkeypatch):
    sid = "aaaa-1"
    d = tmp_path / "projects" / "-x"
    d.mkdir(parents=True)
    (d / "{0}.jsonl".format(sid)).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        _queued("2026-10-09T11:21:00Z", "<ao-session-delivery>[from a] старое</ao-session-delivery>"),
        _queued("2026-10-09T11:25:30Z", "<ao-session-delivery>[from tide-stack-12] Пакет Г "
                "СДАН: 230b37b … 29cfac7</ao-session-delivery>"),
    ]), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sess = tmp_path / "s"
    sess.mkdir()
    (sess / "arc.md").write_text("# s\n\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    out = seed_draft.session_incoming(sess, CTX)
    assert out == ["- поручено мне — от tide-stack-12 в 08:25: Пакет Г СДАН: 230b37b … 29cfac7"]


# --- F34: пульсы курсора старше ПЕРЕДАЧИ помечены ---------------------------------

def test_cursor_pulses_older_than_handover_are_marked():
    lines = seed_draft.block_cursor(["передаю нить"], CTX + ["- 2026-10-09T08:27:00 — шлю номер"])
    assert lines[0] == "передаю нить"
    assert lines[1] == "- 2026-10-09T08:27:00 — шлю номер"
    assert lines[2].startswith("- 2026-10-09T08:26:26 — ПЕРЕДАЧА.")
    assert not lines[2].endswith(seed_draft.BEFORE_PASS)
    assert lines[3] == "- 2026-10-09T08:22:00 — остаток F23/F16, F27 у handoffs · 03 (до ПЕРЕДАЧИ)"
    assert lines[4].endswith("сдала промер (до ПЕРЕДАЧИ)")
    # без ПЕРЕДАЧИ — без пометок
    assert not any(seed_draft.BEFORE_PASS in ln for ln in seed_draft.block_cursor([], CTX[:2]))


# --- C11: карта входа из метки «Карта:» ---------------------------------------------

PASS_WITH_MAP = ("- 2026-10-09T08:26:26 — ПЕРЕДАЧА. Слои освежены. Карта: /abs/plan.md — "
                 "главное и кому сдавать; /abs/topic.md — что ждёт Гриша; /abs/arc.md — "
                 "курсор и пульсы. Опыт: не резать по «;»")


def test_map_label_becomes_entry_map_items():
    labels = seed_draft.labeled_pulses([PASS_WITH_MAP])
    assert labels["entry_map"] == [
        "- /abs/plan.md — главное и кому сдавать (пульс 08:26)",
        "- /abs/topic.md — что ждёт Гриша (пульс 08:26)",
        "- /abs/arc.md — курсор и пульсы (пульс 08:26)",
    ]
    assert labels["experience"] == ["- не резать по «;» (пульс 08:26)"]
    got = seed_draft.block_entry_map(["- старая карта"], labels["entry_map"], ["- собранная"])
    assert got == labels["entry_map"]          # карта ПЕРЕДАЧИ — без каркаса и наследства
    assert not seed_draft.pass_without_map([PASS_WITH_MAP])


def test_pass_without_map_warns_and_collects(tmp_project, capsys):
    import io

    from tide.arc import stream

    ctx = ["- 2026-10-09T08:26:26 — ПЕРЕДАЧА. В полёте: ничего"]
    assert seed_draft.pass_without_map(ctx)
    assert not seed_draft.pass_without_map(["- 2026-10-09T08:20:00 — шаг"])  # нет ПЕРЕДАЧИ
    buf = io.StringIO()
    assert seed_draft.warn_missing_map(ctx, stream=buf)
    assert "нет метки «Карта:»" in buf.getvalue()
    thread = stream.new_thread(tmp_project, "kart", goal="нить с картой")
    (thread / "plan.md").write_text("# plan\n", encoding="utf-8")
    sess = stream.new_session(tmp_project, "kart", "s1", goal="шаг")
    got = seed_draft.collected_map(tmp_project, sess, thread)
    assert len(got) == 3 and all(seed_draft.COLLECTED in ln for ln in got)
    assert got[0].startswith("- {0} — «главное»".format((thread / "plan.md").resolve()))
    assert got[-1].startswith("- {0} — паспорт прошлой сессии".format(
        (sess / "arc.md").resolve()))
    # наследство: собранная карта прошлого сида не переходит дальше
    assert seed_draft.block_entry_map(got, (), ["- новая " + seed_draft.COLLECTED])[0] == \
        "- новая " + seed_draft.COLLECTED

"""SOC-101 F60 и F63 (acceptance-06 §9 пп. 3 и 6).

F60 — «в работе прямо сейчас» (и через F56 «открытые хвосты») выдавали закрытые
входящие за открытые: благодарность tide-stack-26 «нить передана, теперь ведёт …»,
смену адреса tide-stack-31, поручение, на которое уже ответили по делу или которое
записал пульс. Теперь входящее открыто, только если после него нет ответа
отправителю по делу и нет пульса с его id; письма без просьбы — не поручения.

F63 — блок «решения» не наследовал решения входного сида (а «опыт» — да, F22).
Сеть не трогается.
"""

from __future__ import annotations

import json

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


def _delivery(ts: str, who: str, body: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": (
        "<ao-session-delivery id=\"session-send:{0}\">\n[from {1}] {2}\n"
        "</ao-session-delivery>").format(ts, who, body)}}


def _ao_send(ts: str, to: str, text: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{
        "type": "tool_use", "name": "Bash", "input": {
            "command": "ao send --session {0} --message \"{1}\"".format(to, text)}}]}}


def _session(tmp_path, monkeypatch, rows):
    sid = "f60-life"
    d = tmp_path / "projects" / "-ao-tide-stack-25"
    d.mkdir(parents=True)
    t = d / "{0}.jsonl".format(sid)
    t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sess = tmp_path / "s"
    sess.mkdir()
    (sess / "arc.md").write_text("# s\n\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    return sess


CTX = [
    "- 2026-10-09T08:00:00 — нить принята — шов приёма закрыт механикой",
    "- 2026-10-09T11:58:00 — Адреса сменились: continuity-audit · 04 = tide-stack-30, "
    "менеджер management-19. Новые дефекты к работе параллельно.",
    "- 2026-10-09T12:00:00 — ПЕРЕДАЧА. Где мы: пакет сдан",
]

# живой случай пары 13 (09.10, 17:55 по Москве = 14:55Z)
THANKS = ("continuity-audit · 03 → handoffs · 04: спасибо. Нить continuity-audit "
          "передана, теперь её ведёт continuity-audit · 04 = tide-stack-30. Хеш по SOC-98 "
          "и всё дальнейшее шли ей и менеджеру management-18, не мне.")
READDRESS = ("[tide-stack — main · 05] Нить main теперь ведёт main · 05 = tide-stack-31 "
             "(вместо tide-stack-24). Доклады по F55–F61 и пакетам SOC-101 — сюда.")
DEFECTS = ("continuity-audit · 04 → handoffs · 04: из приёмки пары 13 — новые дефекты "
           "F58–F63, подробно в acceptance-06.md §9")
RESTART = "main · 04 → handoffs · 04: перезапусти службу доски и проверь ▶"
OPEN = "board · 05 → handoffs · 04: прошу сверить блок топика на сиде 292"


def test_closed_and_info_incoming_are_not_in_flight(tmp_path, msk, monkeypatch):
    rows = [
        _delivery("2026-10-09T14:55:46Z", "tide-stack-26", THANKS),
        _delivery("2026-10-09T14:55:47Z", "tide-stack-31", READDRESS),
        # записано пульсом 11:58 по id отправителя
        _delivery("2026-10-09T14:55:48Z", "tide-stack-30", DEFECTS),
        # ответ по делу отправителю закрывает
        _delivery("2026-10-09T11:50:00Z", "tide-stack-24", RESTART),
        _ao_send("2026-10-09T11:55:00Z", "tide-stack-24",
                 "handoffs · 04 → main · 04: служба доски перезапущена, ▶ на месте"),
        # открытое: просьба, ответа нет, пульса с id нет
        _delivery("2026-10-09T14:56:00Z", "tide-stack-33-ab", OPEN),
    ]
    sess = _session(tmp_path, monkeypatch, rows)
    out = seed_draft.session_incoming(sess, CTX)
    orders = [ln for ln in out if ln.startswith("- поручено мне")]
    assert [ln.split(" в ")[0] for ln in orders] == ["- поручено мне — от tide-stack-33-ab"]
    text = "\n".join(out)
    for gone in ("tide-stack-26", "tide-stack-31", "tide-stack-30", "tide-stack-24"):
        assert gone not in text
    # F56: хвосты строятся из «в работе» — закрытых там тоже нет
    tails = seed_draft.merge_tails([], out)
    assert len(tails) == 1 and "tide-stack-33-ab" in tails[0]


def test_info_letter_vs_order():
    assert seed_draft.is_info_letter(THANKS)
    assert seed_draft.is_info_letter(READDRESS)
    assert not seed_draft.is_info_letter(DEFECTS)
    assert not seed_draft.is_info_letter(OPEN)
    # передача с находкой — уже не просто письмо
    assert not seed_draft.is_info_letter(
        "main · 04 → handoffs · 04: нить main передана, теперь ведёт main · 05. "
        "Мелкая находка: детектор не видит метку в середине строки.")


def test_reply_closes_unless_bare_receipt_or_promise():
    assert seed_draft.closes_by_reply(
        'ao send --session x-1 --message "F58–F63 приняты. F58 — у сабагента вместе с '
        'F55, F61+F62 у второго"')
    assert not seed_draft.closes_by_reply('ao send --session x-1 --message "принято, спасибо"')
    assert not seed_draft.closes_by_reply("разбор получен, сведу к 16:00")
    assert seed_draft.closes_by_reply("пакет сдан: 4ce0867, pytest 2884 passed")


def test_pulse_names_sender_by_id_not_prefix(msk):
    from datetime import datetime

    at = datetime(2026, 10, 9, 11, 0)
    pulses = seed_draft._pulse_stamps(["- 2026-10-09T11:30:00 — сдала tide-stack-24 хеш"])
    assert seed_draft.pulse_names_sender("tide-stack-24-9e", at, pulses)
    assert not seed_draft.pulse_names_sender("tide-stack-2", at, pulses)
    # пульс раньше доставки — не закрывает
    assert not seed_draft.pulse_names_sender("tide-stack-24", datetime(2026, 10, 9, 12), pulses)
    # «запросила у X, жду к 15:15» — ожидание от X, а не запись его письма
    wait = seed_draft._pulse_stamps(["- 2026-10-09T11:30:00 — разбор запрошен у "
                                     "tide-stack-24 к 15:15"])
    assert not seed_draft.pulse_names_sender("tide-stack-24", at, wait)


def test_mention_inside_message_is_not_a_reply():
    """«ao send --session X» в тексте письма к Y — не ответ X."""
    row = {"type": "assistant", "message": {"content": [{
        "type": "tool_use", "name": "Bash", "input": {"command": (
            'ao send --session management-15 --message "сдано; пиши через '
            'ao send --session tide-stack-24 --message"')}}]}}
    assert [to for to, _ in seed_draft._replies(row)] == ["management-15"]

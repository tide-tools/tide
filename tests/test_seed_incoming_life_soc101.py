"""SOC-101 F29 (жизнь сессии) — входящие за всю жизнь передающей сессии доходят до сида.

Живой случай: continuity-audit · 02 → 03 (оффер 273, 09.10). Разборы SOC-127 от
mitehq-2, music-15 и tide-stack-24 пришли сессии, она ответила «получен» и передала
нить; в сид не дошёл ни один — окно входящих открывалось пульсом перед ПЕРЕДАЧЕЙ.
Теперь окно — с приёма сессии, закрывает входящее только ответ-итог отправителю или
пульс, который его уже несёт. Сеть не трогается.
"""

from __future__ import annotations

import json
from datetime import datetime

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


def _uds(ts: str, who: str, body: str) -> dict:
    return {"type": "attachment", "timestamp": ts, "attachment": {
        "type": "queued_command", "commandMode": "prompt", "prompt": (
            "<cross-session-message from=\"uds:/tmp/x.sock\" from-name=\"{0}\" "
            "from-mode=\"bypass\">\n{1}\n</cross-session-message>").format(who, body)}}


def _ao_send(ts: str, to: str, text: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{
        "type": "tool_use", "name": "Bash", "input": {
            "command": "ao send --session {0} --message \"{1}\"".format(to, text)}}]}}


def _send_message(ts: str, to: str, text: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{
        "type": "tool_use", "name": "SendMessage",
        "input": {"to": to, "summary": "x", "message": text}}]}}


MUSIC = ("music-15 (music — main · 18) → continuity-audit 02, SOC-127, разбор music: "
         "(1) Буксовали: красный mac2 у h177 заметили через 1 ч 45 мин; (2) топ-расход — "
         "сабагент ждал ответа. " + "Подробности прогона и меры по каждому пункту. " * 8)

# Пульсы передающей сессии: приём 08:00, работа, ПЕРЕДАЧА 08:30, хвосты 08:31.
CTX = [
    "- 2026-10-09T08:00:00 — нить принята — шов приёма закрыт механикой",
    "- 2026-10-09T08:12:00 — SOC-127: разборы запрошены у music-15, mitehq-2, "
    "tide-stack-24 к 15:15",
    "- 2026-10-09T08:22:00 — сводка расхода: воркер пишет workspace/soc127-data.md",
    "- 2026-10-09T08:24:00 — разбор app-development (app-development-1): main живёт "
    "13 часов без хендоффа, перечитывание кэша главная утечка",
    "- 2026-10-09T08:30:00 — ПЕРЕДАЧА continuity-audit · 02 → 03. Где мы: SOC-127 "
    "итог не сдан",
    "- 2026-10-09T08:31:00 — Хвосты: SOC-127 итог не сдан (срок 16:00)",
]

ROWS = [
    # до приёма сессии (больше 10 мин) — не её
    _delivery("2026-10-09T10:30:00Z", "old-1", "чужое старое поручение до приёма"),
    # три разбора SOC-127 до ПЕРЕДАЧИ, между ними пульсы; на каждый — «получен»
    _delivery("2026-10-09T11:05:00Z", "mitehq-2", "[bidbook·04 → SOC-127] Буксовали: "
              "документ для владельца переписан 4 раза — v2.10 (48 правок)"),
    _ao_send("2026-10-09T11:05:30Z", "mitehq-2", "спасибо, разбор получен"),
    _delivery("2026-10-09T11:15:00Z", "music-15", MUSIC),
    _send_message("2026-10-09T11:15:40Z", "music-15-ab [12ab34]",
                  "music-15: разбор получен, сведу к 16:00"),
    _delivery("2026-10-09T11:26:00Z", "tide-stack-24", "main · 04 → continuity-audit · 02, "
              "SOC-127, разбор по tide-stack: топ-расход — сессии без хендоффа"),
    _ao_send("2026-10-09T11:26:20Z", "tide-stack-24", "принято, получен, спасибо"),
    # закрыто итогом отправителю — не в сиде
    _delivery("2026-10-09T11:07:00Z", "x-1", "проверь пару 12 и пришли вердикт"),
    _ao_send("2026-10-09T11:20:00Z", "x-1", "вердикт: пара 12 принята, коммент a1b2c3d"),
    # уже записано пульсом 08:24 — не дублируется
    _uds("2026-10-09T11:23:00Z", "app-development-1-da", "app-development-1 (main · 01) → "
         "tide-stack-20, разбор по SOC-127: main живёт 13 часов без хендоффа, "
         "перечитывание кэша — главная утечка"),
]


def _session(tmp_path, monkeypatch, rows):
    sid = "6469008f-life"
    d = tmp_path / "projects" / "-ao-tide-stack-20"
    d.mkdir(parents=True)
    t = d / "{0}.jsonl".format(sid)
    t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    sess = tmp_path / "s"
    sess.mkdir()
    (sess / "arc.md").write_text("# s\n\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    return sess, t


def test_three_reports_before_handover_all_reach_the_seed(tmp_path, msk, monkeypatch):
    sess, t = _session(tmp_path, monkeypatch, ROWS)
    out = seed_draft.session_incoming(sess, CTX)
    orders = [ln for ln in out if ln.startswith("- поручено мне")]
    assert [ln.split(" в ")[0] for ln in orders] == [
        "- поручено мне — от tide-stack-24", "- поручено мне — от music-15",
        "- поручено мне — от mitehq-2"]
    assert orders[0].startswith("- поручено мне — от tide-stack-24 в 08:26: main · 04 → ")
    assert orders[1].startswith("- поручено мне — от music-15 в 08:15: music-15 (music")
    assert orders[2].startswith("- поручено мне — от mitehq-2 в 08:05: [bidbook·04 → SOC-127]")
    # бюджет: длинный разбор — началом (~200 знаков) + путь к полному тексту
    assert len(orders[1]) < 260 and orders[1].endswith("…")
    assert out[-1] == "- (полные тексты входящих — транскрипт {0})".format(t)
    text = "\n".join(out)
    assert "old-1" not in text                # до приёма сессии
    assert "x-1" not in text                  # закрыто итогом отправителю
    assert "app-development" not in text      # уже несёт пульс 08:24


def test_old_window_from_the_pulse_before_handover_lost_them(tmp_path, msk, monkeypatch):
    """Регресс: окно «с пульса перед ПЕРЕДАЧЕЙ» (08:24) теряло два разбора из трёх."""
    sess, t = _session(tmp_path, monkeypatch, ROWS)
    assert seed_draft.incoming_since(CTX) == datetime(2026, 10, 9, 8, 24)
    old = seed_draft.incoming_orders(t, seed_draft.incoming_since(CTX), CTX)
    assert [ln.split(" в ")[0] for ln in old] == ["- поручено мне — от tide-stack-24"]
    assert seed_draft.session_taken_at(CTX) == datetime(2026, 10, 9, 8, 0)


def test_ack_does_not_close_but_a_result_does():
    assert not seed_draft.is_closing_reply("спасибо, разбор получен и переслан")
    assert not seed_draft.is_closing_reply("SOC-127 взяла, итог: пришлю к 16:00")
    assert seed_draft.is_closing_reply("пакет сдан: 4ce0867, pytest 2884 passed")
    assert seed_draft.is_closing_reply("вердикт: принят с замечаниями")


def test_sender_key_and_speaker_label():
    assert seed_draft.sender_key("app-development-1-da") == "app-development-1"
    assert seed_draft.sender_key("management-16-61 [4a4d71]") == "management-16"
    assert seed_draft.sender_key("tide-stack-24") == "tide-stack-24"
    assert seed_draft.speaker_label("handoffs · 03 → continuity-audit · 02: x") == \
        "handoffs · 03"
    assert seed_draft.speaker_label("[tide-stack — board · 03, tide-stack-23 → c] x") == \
        "board · 03"


def test_batched_and_pasted_deliveries_in_one_turn_are_all_seen(tmp_path, msk, monkeypatch):
    """Живой формат 17:06: пачка доставок одной репликой, часть — в <pasted_content>."""
    body = ("<ao-session-delivery id=\"a\">\n[from mitehq-2] [bidbook·04 → SOC-127] раз\n"
            "</ao-session-delivery>\n<ao-session-delivery id=\"b\">\n[from tide-stack-24] "
            "main · 04 → continuity-audit · 02: два\n</ao-session-delivery>\n\n\n"
            "<pasted_content id=\"8593\">\n<ao-session-delivery id=\"c\">\n[from music-15] "
            "три\n</ao-session-delivery><ao-session-delivery id=\"d\">\n[from tide-stack-24] "
            "четыре\n</ao-session-delivery>\n</pasted_content>")
    sess, t = _session(tmp_path, monkeypatch, [
        {"type": "user", "timestamp": "2026-10-09T11:40:00Z", "message": {"content": body}}])
    out = seed_draft.session_incoming(sess, CTX)
    assert [ln.split(": ", 1)[1] for ln in out] == [
        "[bidbook·04 → SOC-127] раз", "main · 04 → continuity-audit · 02: два", "три",
        "четыре"]

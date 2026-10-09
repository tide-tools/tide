"""SOC-101, слово Гриши (tg 3119): ровно одно сообщение в Telegram на приём нити.

Повод — первый настоящий пульс принявшей сессии (он ставит ``first-pulse:`` в
оффер). Канал — ``$TIDE_ALERT_CMD`` (здесь подменён: ничего не уходит наружу).
Метка ``announced:`` в оффере — второй пульс не шлёт. Нет команды — тишина и
строка в журнале. Принимающая не поднялась — одно «не поднялась, нить остаётся у …».
"""

from __future__ import annotations

import pytest

from tide import cli, fields, handoff_queue as hq, offload, paths
from tide.arc import stream
from tide.launcher import autohandoff, handoff, pickup, reception

from tests.test_handoff_autospawn import _FakeAo, _use, world  # noqa: F401  fixture

SEED = """# seed

## главное
- SOC-101

## ожидание Гриши
Гриша ждёт живой шов передачи без ручных пересылок, к вечеру пятницы.

## следующий шаг
- дописать пробу живости и тест на моках AO

## в работе прямо сейчас
- воркер пакета Б гоняет pytest

## где мы
шаг 3 из 7
"""


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(text, *, route, source="", extra_env=None):
        calls.append({"text": text, "route": route, "source": source})
        return 0

    monkeypatch.setattr(autohandoff, "send_alert", fake_send)
    monkeypatch.setenv(autohandoff.ALERT_CMD_ENV, "never-run")
    return calls


@pytest.fixture
def seam(world, monkeypatch):
    proj, home = world["proj"], world["home"]
    old = stream.new_session(proj, "seam", "old-work", goal="old work")
    fields.set_field(old / "arc.md", "claude-session", "old-sid")
    fields.set_field(old / "arc.md", "title", "proj — seam · 01")
    new = stream.new_session(proj, "seam", "seam-2", goal="живой шов передачи")
    fields.set_field(new / "arc.md", "title", "proj — seam · 02")
    (new / "input").mkdir(exist_ok=True)
    seed = new / "input" / "handoff-seed.md"
    seed.write_text(SEED, encoding="utf-8")
    hq.offer(home, "seam", arc="01-@seam/" + new.name, project="proj", seed=str(seed),
             from_session="old-sid")
    key = hq.list_offers(home)[0]["name"]
    hq.reserve(home, key, session="new-sid")
    monkeypatch.setattr(offload, "_main_gate", lambda root, ref: None)  # not under test
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "new-sid")
    return {"home": home, "key": key, "new": new, "old": old}


def _pulse(text="пишу пробу живости"):
    return cli.main(["offload", "seam/seam-2", "--cursor", text])


def test_one_message_on_reception_and_none_on_repeat(seam, sent):
    assert _pulse() == 0
    assert hq.list_offers(seam["home"])[0]["status"] == hq.STATUS_TAKEN
    assert len(sent) == 1
    msg = sent[0]["text"]
    assert sent[0]["route"] == "human"
    assert msg.startswith("proj — seam · 02 приняла нить от proj — seam · 01. Передано: ")
    assert "следующий шаг — дописать пробу живости и тест на моках AO" in msg
    assert "ожидание Гриши — Гриша ждёт живой шов передачи" in msg
    assert "в полёте — воркер пакета Б гоняет pytest" in msg
    assert "Взято: живой шов передачи" in msg
    assert "Потеряно: " in msg
    rec = hq.list_offers(seam["home"])[0]
    assert "sent" in hq._get(rec["path"].read_text(encoding="utf-8"), reception.ANNOUNCED)
    # второй и третий пульс — ни одного нового сообщения
    assert _pulse("дальше") == 0
    assert _pulse("ещё дальше") == 0
    assert len(sent) == 1


def test_direct_repeat_is_silent(seam, sent):
    hq.take(seam["home"], seam["key"], session="new-sid")
    assert reception.announce_reception(seam["home"], seam["key"]) == "sent"
    assert reception.announce_reception(seam["home"], seam["key"]) is None
    assert len(sent) == 1


def test_no_alert_cmd_skips_quietly_and_logs(seam, monkeypatch):
    calls = []
    monkeypatch.setattr(autohandoff, "send_alert", lambda *a, **k: calls.append(a) or 0)
    monkeypatch.delenv(autohandoff.ALERT_CMD_ENV, raising=False)
    assert _pulse() == 0
    assert calls == []
    rec = hq.list_offers(seam["home"])[0]
    assert hq._get(rec["path"].read_text(encoding="utf-8"), reception.ANNOUNCED).endswith(
        "no-cmd")
    log = paths.state_dir(seam["home"]) / reception.ALERT_LOG
    assert "приняла нить от" in log.read_text(encoding="utf-8")


def test_fields_are_clipped_to_about_200():
    long = "слово " * 100
    msg = reception.reception_text(new_title="A", old_title="B", nxt=long,
                                   expectation="e", inflight="—", taken="t", lost=[])
    part = msg.split("следующий шаг — ", 1)[1].split("; ожидание", 1)[0]
    assert len(part) <= 200 and part.endswith("…")
    assert msg.endswith("Потеряно: ничего.")


def test_lost_lists_missing_audit_sections(seam, sent, monkeypatch):
    from tide.launcher import handoff_audit

    monkeypatch.setattr(handoff_audit, "audit", lambda home, key: {"sections": [
        {"key": "next", "title": "следующий шаг", "status": "ok"},
        {"key": "dialog", "title": "неотвеченные реплики Гриши", "status": "missing"},
        {"key": "understood", "title": "понятая цель", "status": "missing"},
    ]})
    hq.take(seam["home"], seam["key"], session="new-sid")
    reception.announce_reception(seam["home"], seam["key"])
    assert sent[0]["text"].endswith("Потеряно: неотвеченные реплики Гриши.")


def test_not_alive_sends_one_message(world, monkeypatch, sent):
    proj, home = world["proj"], world["home"]
    old = stream.new_session(proj, "seam", "old-work", goal="old work")
    fields.set_field(old / "arc.md", "claude-session", "old-sid")
    fields.set_field(old / "arc.md", "title", "proj — seam · 01")

    class _Dead(_FakeAo):
        def first_signal(self, handle):
            return False

        def resume_agent(self, handle):
            return True

    _use(monkeypatch, _Dead(ref="proj-7"))
    monkeypatch.setattr(handoff, "_sleep", lambda s: None)
    monkeypatch.setattr(handoff, "_clock", iter(range(0, 10_000, 50)).__next__)
    assert cli.main(["handoff", "seam", "--from-session", "old-sid"]) == 0
    assert len(sent) == 1
    msg = sent[0]["text"]
    assert msg.endswith("не поднялась, нить остаётся у proj — seam · 01")
    assert msg.startswith("proj — seam · ")

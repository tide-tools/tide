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
    assert msg.splitlines()[0] == "proj — seam · 02 приняла у · 01"
    # F35: «Взято» — слова первого пульса принявшей, не goal паспорта (эхо сида)
    assert "Взято: пишу пробу живости" in msg
    assert "живой шов передачи" not in msg
    assert "Потеряно: " in msg
    assert "воркер пакета Б" not in msg and "input/" not in msg  # не хвост сида
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
    assert "приняла у" in log.read_text(encoding="utf-8")


def _assert_short(msg):
    assert len(msg.splitlines()) <= 4, msg
    assert len(msg) <= 600, (len(msg), msg)


def test_reception_message_is_short_lines():
    long = "слово " * 100
    msg = reception.reception_text(
        new_title="tide-stack — handoffs · 03 · tide-stack-21",
        old_title="tide-stack — handoffs · 02", taken=long,
        lost=["неотвеченные реплики Гриши " * 10])
    _assert_short(msg)
    first, taken, lost = msg.splitlines()
    assert first == "tide-stack — handoffs · 03 · tide-stack-21 приняла у · 02"
    assert taken.startswith("Взято: ") and taken.endswith("…")
    assert len(taken) <= len("Взято: ") + reception.TAKEN_MAX
    assert lost.startswith("Потеряно: ")
    ok = reception.reception_text(new_title="A — t · 02", old_title="B — u · 05",
                                  taken="t", lost=[])
    # F36: аудит видит только разделы — «ничего» всегда с мерой, не голое
    assert ok == ("A — t · 02 приняла у B — u · 05\nВзято: t\n"
                  "Потеряно: по сверке разделов — ничего")
    unknown = reception.reception_text(new_title="A — t · 02", old_title="B — u · 05",
                                       taken="t", lost=None)
    assert unknown.endswith("Потеряно: сверка разделов не прошла — не знаю")
    assert "Потеряно: ничего" not in unknown


def test_live_reception_message_is_short(seam, sent):
    assert _pulse("слово " * 80) == 0
    msg = sent[0]["text"]
    _assert_short(msg)
    assert "приняла у" in msg and "Потеряно:" in msg


def test_not_alive_text_is_two_short_lines():
    msg = reception.not_alive_text("tide-stack — handoffs · 03 · tide-stack-21",
                                   "tide-stack — handoffs · 02")
    lines = msg.splitlines()
    assert len(lines) == 2 and len(msg) <= 300
    assert lines[0] == "tide-stack — handoffs · 03 · tide-stack-21 не поднялась, нить остаётся у · 02"
    assert "tide-stack — handoffs · 02 работает дальше" in lines[1]


def test_lost_lists_missing_audit_sections(seam, sent, monkeypatch):
    from tide.launcher import handoff_audit

    monkeypatch.setattr(handoff_audit, "audit", lambda home, key: {"sections": [
        {"key": "next", "title": "следующий шаг", "status": "ok"},
        {"key": "dialog", "title": "неотвеченные реплики Гриши", "status": "missing"},
        {"key": "understood", "title": "понятая цель", "status": "missing"},
    ]})
    hq.take(seam["home"], seam["key"], session="new-sid")
    reception.announce_reception(seam["home"], seam["key"])
    assert sent[0]["text"].endswith(
        "Потеряно: по сверке разделов: неотвеченные реплики Гриши")


def test_failed_audit_is_not_nothing_lost(seam, sent, monkeypatch):
    from tide.launcher import handoff_audit

    def _boom(home, key):
        raise RuntimeError("нет сида")
    monkeypatch.setattr(handoff_audit, "audit", _boom)
    hq.take(seam["home"], seam["key"], session="new-sid")
    reception.announce_reception(seam["home"], seam["key"])
    assert sent[0]["text"].endswith("Потеряно: сверка разделов не прошла — не знаю")


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
    first = msg.splitlines()[0]
    assert first.endswith("не поднялась, нить остаётся у · 01")
    assert msg.startswith("proj — seam · ")
    assert len(msg.splitlines()) == 2


# --- выключатель $TIDE_ACCEPT_NOTIFY (all | fail | off) ------------------------------

def test_notify_fail_skips_the_reception_message(seam, sent, monkeypatch):
    monkeypatch.setenv(reception.NOTIFY_ENV, "fail")
    assert _pulse() == 0
    assert sent == []
    rec = hq.list_offers(seam["home"])[0]
    assert hq._get(rec["path"].read_text(encoding="utf-8"), reception.ANNOUNCED).endswith("off")
    assert _pulse("дальше") == 0
    assert sent == []


def test_notify_fail_still_reports_not_alive_and_off_silences_it(seam, sent, monkeypatch):
    monkeypatch.setenv(reception.NOTIFY_ENV, "fail")
    assert reception.announce_not_alive(seam["home"], seam["key"]) == "sent"
    assert len(sent) == 1 and "не поднялась" in sent[0]["text"]
    monkeypatch.setenv(reception.NOTIFY_ENV, "off")
    assert reception.announce_not_alive(seam["home"], seam["key"]) == "off"
    assert len(sent) == 1


def test_notify_default_and_garbage_mean_all(monkeypatch):
    monkeypatch.delenv(reception.NOTIFY_ENV, raising=False)
    assert reception.notify_mode() == "all"
    monkeypatch.setenv(reception.NOTIFY_ENV, "loud")
    assert reception.notify_mode() == "all"


# --- F41: «Передано», «Взято» целыми фразами, открытые хвосты в «Потеряно» ----------

def test_reception_has_passed_line_and_whole_sentences():
    msg = reception.reception_text(
        new_title="A — t · 04", old_title="A — t · 03",
        taken="Принял нить t у 03 (оффер 263). main · 04 подтвердила: поручений нет. "
              "Цель: сдать SOC-101 через перемер.",
        lost=[], passed="SOC-101 In Progress; в полёте: перемер")
    first, passed, taken, lost = msg.splitlines()
    assert first == "A — t · 04 приняла у · 03"
    assert passed == "Передано: SOC-101 In Progress; в полёте: перемер"
    # эхо «Принял нить … у 03» выкинуто — оно и так в первой строке; фразы целые
    assert taken == "Взято: main · 04 подтвердила: поручений нет. Цель: сдать SOC-101 через перемер."
    assert lost == "Потеряно: по сверке разделов — ничего"


def test_taken_cuts_on_sentence_not_mid_word():
    long = ("main · 04 подтвердила: старшая по нити, поручений нет; моя приёмка — вторая "
            "пара промера SOC-101 у continuity-audit · 02 (tide-stack-20). " + "слово " * 60)
    out = reception.taken_text(long)
    assert out.endswith("(tide-stack-20).")
    assert reception.sentences("см. файл. Дальше т.е. так. Конец") == [
        "см. файл.", "Дальше т.е. так.", "Конец"]


PASS_NOTE = ("ПЕРЕДАЧА handoffs · 03 → 04. Где мы: SOC-101 (Linear In Progress). Первая пара "
             "«с замечаниями» — main 03→04 (оффер 260). По её §9 сдан пакет Д: " + "x " * 80 +
             ". В полёте: перемер continuity-audit · 02 — вердикт придёт новой. Ждёт Гришу: "
             "SOC-57. Карта: /a — зачем")


def test_passed_from_pass_note():
    out = reception.passed_from_note(PASS_NOTE)
    assert out == ("SOC-101 (Linear In Progress). Первая пара «с замечаниями» — main 03→04 "
                   "(оффер 260); в полёте: перемер continuity-audit · 02 — вердикт придёт новой")
    assert reception.passed_from_note("") == ""


def test_live_passed_from_old_pass_pulse(seam, sent):
    offload.write_pulse(seam["old"], note=PASS_NOTE)
    assert _pulse() == 0
    msg = sent[0]["text"]
    _assert_short(msg)
    assert msg.splitlines()[1].startswith("Передано: SOC-101 (Linear In Progress).")


def test_live_passed_falls_back_to_seed_where(seam, sent):
    assert _pulse() == 0
    assert sent[0]["text"].splitlines()[1] == "Передано: шаг 3 из 7"


TAILS = """
## открытые хвосты
- tide verify --portable красный
- Д2 не проверено вживую
  (продолжение пункта)
- обещано: сверка C4 с Linear
"""


def test_open_tails_parsed_and_absent_or_empty_is_nothing():
    assert reception.open_tails(SEED + TAILS) == [
        "tide verify --portable красный", "Д2 не проверено вживую",
        "обещано: сверка C4 с Linear"]
    assert reception.open_tails(SEED) == []
    assert reception.open_tails(SEED + "\n## открытые хвосты\n- нет\n") == []
    assert reception.open_tails(SEED + "\n## открытые хвосты\n—\n\n## окружение\n- x\n") == []


def test_lost_names_open_tails_not_nothing(seam, sent):
    seed = seam["new"] / "input" / "handoff-seed.md"
    seed.write_text(SEED + TAILS, encoding="utf-8")
    assert _pulse() == 0
    lost = sent[0]["text"].splitlines()[-1]
    assert lost.startswith("Потеряно: по сверке разделов")
    assert lost.endswith("; открытых хвостов 3: tide verify --portable красный (и ещё 2)")
    assert "Потеряно: по сверке разделов — ничего" + "\n" not in sent[0]["text"]

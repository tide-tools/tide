"""Ложный стоп автохендоффа (09.10, разбор continuity-audit-2 srez-09-10 §2).

По контракту v1 работа живёт в Linear: нити main/management/tide-stack не чекают
пунктов работ tide и не пишут «акт:», поэтому прогресс был 0 и каждая четвёртая
автопередача вставала STOP_STALLED. Теперь продвижение — ещё и содержательные
пульсы сессий нити (механика приёма/передачи не в счёт), а стоп сразу пишет
менеджеру ``ao send``.

Ни одного настоящего ``ao send`` / Telegram: ``_ao_send`` и адрес менеджера
подменены, проверка формы команды — на фейковом бинаре ``$TIDE_AO_BIN``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tide import cli, fields, offload, whois
from tide import handoff_queue as hq
from tide.arc import stream
from tide.launcher import autohandoff as ah, handoff, pickup

from tests.test_autohandoff import SID, _Fake, _work, world  # noqa: F401 — fixture

# сохранено при импорте — до любых подмен в тестах
REAL_AO_SEND = ah._ao_send


def _pulse(sess: Path, note: str) -> None:
    offload.write_pulse(sess, note=note)


def _mechanics(sess: Path) -> None:
    """Всё, что пишет механика (и сама передача) — без единого шага работы."""
    _pulse(sess, hq.RECEPTION_PULSE)
    _pulse(sess, "{0} (оффер 261-04-main)".format(handoff.NOT_ALIVE_LINE))
    _pulse(sess, "ПЕРЕДАЧА. Ждёт Гришу: — В полёте: — Карта: /tmp/x — зачем")


@pytest.fixture
def mgr(monkeypatch):
    """Менеджер есть (как ``tide whois manager``), ao send перехвачен."""
    sent = []
    monkeypatch.setattr(whois, "manager", lambda **kw: {
        "ao_session": "management-15", "title": "management — main · 05"})
    monkeypatch.setattr(ah, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    return sent


# --- 1. продвижение -----------------------------------------------------------

def test_mechanical_pulses_are_not_progress(world):
    _mechanics(world["sess"])
    assert ah.count_progress(world["proj"], world["thread"]) == 0


def test_content_pulses_are_progress_across_sessions_monotonic(world):
    _mechanics(world["sess"])
    _pulse(world["sess"], "SOC-112 In Review, пруф: скрин доски")
    _pulse(world["sess"], "сдал пакет Е; передача — завтра")  # ПЕРЕДАЧА не в начале
    first = ah.count_progress(world["proj"], world["thread"])
    assert first == 2
    s2 = stream.new_session(world["proj"], "seam", "s2")
    _mechanics(s2)
    assert ah.count_progress(world["proj"], world["thread"]) == first  # не убывает
    _pulse(s2, "курсор → разбираю очередь SOC")
    assert ah.count_progress(world["proj"], world["thread"]) == first + 1


def test_chain_resets_when_session_pulsed(world):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, ah.AUTO_CHAIN_FIELD, "3")
    fields.set_field(pp, ah.PROGRESS_MARK_FIELD, "0")
    _mechanics(world["sess"])
    _pulse(world["sess"], "менеджеру отдал сводку, SOC-100 Done")
    d = ah.decide_for(world["sess"])
    assert d.spawn and d.chain == 1 and d.progress == 1


def test_loop_guard_still_stops_mechanics_only_session(world):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, ah.AUTO_CHAIN_FIELD, "3")
    fields.set_field(pp, ah.PROGRESS_MARK_FIELD, "0")
    _mechanics(world["sess"])
    d = ah.decide_for(world["sess"])
    assert not d.spawn and d.reason == ah.STOP_STALLED
    assert "пульсов" in d.detail


# --- 2. стоп пишет менеджеру ----------------------------------------------------

def test_signal_stall_tells_manager(world, mgr):
    out = ah.signal_stall(world["proj"], world["thread"], "3 автопередачи подряд",
                          offer="261-04-main")
    assert out["manager"] == {"to": "management-15", "code": 0}
    ((handle, text),) = mgr
    assert handle == "management-15"
    assert "seam" in text and "3 автопередачи подряд" in text
    assert "261-04-main" in text and "tide pickup 261-04-main" in text
    assert out["artifact"] is not None


def test_signal_stall_without_manager_is_silent(world, monkeypatch):
    called = []
    monkeypatch.setattr(whois, "manager", lambda **kw: {"ao_session": ""})
    monkeypatch.setattr(ah, "_ao_send", lambda h, t: called.append(h) or 0)
    out = ah.signal_stall(world["proj"], world["thread"], "вопрос", offer="7-x")
    assert out["manager"] == {"to": None, "code": None} and not called


def test_signal_stall_never_raises(world, monkeypatch):
    def boom(**kw):
        raise RuntimeError("нет home")
    monkeypatch.setattr(whois, "manager", boom)
    assert ah.signal_stall(world["proj"], world["thread"], "x")["manager"]["to"] is None
    monkeypatch.setattr(whois, "manager", lambda **kw: {"ao_session": "m-1"})

    def bad_send(h, t):
        raise OSError("ao умер")
    monkeypatch.setattr(ah, "_ao_send", bad_send)
    assert ah.signal_stall(world["proj"], world["thread"], "x")["manager"] == {
        "to": "m-1", "code": -1}


def test_ao_send_form_on_fake_binary(tmp_path, monkeypatch):
    sink = tmp_path / "argv.txt"
    fake = tmp_path / "ao"
    fake.write_text("#!{0}\nimport sys; open({1!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
                    .format(sys.executable, str(sink)), encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("TIDE_AO_BIN", str(fake))
    assert REAL_AO_SEND("music-32", "нить встала") == 0
    assert sink.read_text(encoding="utf-8").splitlines() == [
        "send", "--session", "music-32", "--message", "нить встала"]
    monkeypatch.setenv("TIDE_AO_BIN", str(tmp_path / "nope"))
    assert REAL_AO_SEND("m", "t") is None


def test_auto_stop_reaches_manager_end_to_end(world, monkeypatch, capsys, mgr):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, ah.AUTO_CHAIN_FIELD, "3")
    fields.set_field(pp, ah.PROGRESS_MARK_FIELD, "0")
    adapter = _Fake()
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: adapter)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    assert adapter.spawned is None
    (rec,) = hq.list_offers(world["home"])
    ((handle, text),) = mgr
    assert handle == "management-15" and "tide pickup {0}".format(rec["name"]) in text


# --- 3. автоподъём вставшей нити через 30 мин ------------------------------------------

def _offer(world, slug="seam-x"):
    seed = world["tmp"] / "seed.md"
    seed.write_text("# сид\n", encoding="utf-8")
    path = hq.offer(world["home"], slug, arc="{0}/01-s1".format(world["thread"].name),
                    project="proj", seed=str(seed))
    return next(r for r in hq.list_offers(world["home"]) if r["name"] == path.stem)


@pytest.fixture
def raise_spy(monkeypatch, mgr):
    """Подъём, сон, TG и ao перехвачены; ничего настоящего."""
    spy = {"raised": [], "slept": [], "alerts": []}
    monkeypatch.setattr(ah, "_raise_offer", lambda home, key, **kw: spy["raised"].append(key)
                        or {"ok": True, "action": "spawned", "detail": "fake"})
    monkeypatch.setattr(ah, "send_alert", lambda text, **kw: spy["alerts"].append(
        (kw.get("route"), text)) or 0)
    spy["mgr"] = mgr
    return spy


def test_autoraise_after_env(monkeypatch):
    monkeypatch.delenv(ah.AUTORAISE_ENV, raising=False)
    assert ah.autoraise_after() == 1800
    for off in ("0", "off", "OFF", "no"):
        monkeypatch.setenv(ah.AUTORAISE_ENV, off)
        assert ah.autoraise_after() == 0
    monkeypatch.setenv(ah.AUTORAISE_ENV, "90")
    assert ah.autoraise_after() == 90


def test_autoraise_raises_untaken_offer_and_tells(world, raise_spy):
    rec = _offer(world)
    out = ah.run_autoraise(world["home"], rec["name"], after=1800, thread="seam",
                           sleep=raise_spy["slept"].append)
    assert out["action"] == "raised" and raise_spy["raised"] == [rec["name"]]
    assert sum(raise_spy["slept"]) == 1800 and max(raise_spy["slept"]) <= ah.AUTORAISE_POLL
    ((route, text),) = raise_spy["alerts"]
    assert route == ah.ROUTE_LOG  # tg 3733: удачный подъём — журнал, не Грише
    assert "нить seam стояла 30 мин" in text and "поднял" in text and rec["name"] in text
    ((handle, mtext),) = raise_spy["mgr"]
    assert handle == "management-15" and mtext == text


def test_autoraise_exits_quietly_once_taken(world, raise_spy):
    rec = _offer(world)
    calls = []

    def sleep(sec):
        calls.append(sec)
        if len(calls) == 2:  # ответили на второй минуте
            hq.take(world["home"], rec["name"], session="sid-x")
    out = ah.run_autoraise(world["home"], rec["name"], after=1800, thread="seam",
                           sleep=sleep)
    assert out["action"] == "skip" and out["state"] == "taken"
    assert len(calls) == 2  # вышел сразу, не досыпал 30 минут
    assert not raise_spy["raised"] and not raise_spy["alerts"] and not raise_spy["mgr"]


def test_autoraise_skips_gone_offer(world, raise_spy):
    out = ah.run_autoraise(world["home"], "999-net", after=60, sleep=raise_spy["slept"].append)
    assert out["state"] == "gone" and not raise_spy["slept"] and not raise_spy["raised"]


def test_autoraise_failed_pickup_still_tells(world, raise_spy, monkeypatch):
    rec = _offer(world)
    monkeypatch.setattr(ah, "_raise_offer", lambda home, key, **kw: {
        "ok": False, "action": "failed", "detail": "адаптер умер"})
    out = ah.run_autoraise(world["home"], rec["name"], after=0, thread="seam")
    assert out["action"] == "failed"
    ((route, text),) = raise_spy["alerts"]
    assert route == ah.ROUTE_HUMAN  # не поднял — нить всё ещё стоит, Грише
    assert "адаптер умер" in text and "tide pickup {0}".format(rec["name"]) in text


def test_signal_stall_schedules_detached_autoraise(world, mgr, monkeypatch):
    spawned = []
    monkeypatch.setenv(ah.AUTORAISE_ENV, "1800")
    monkeypatch.setattr(ah, "_spawn_autoraise",
                        lambda home, offer, thread, after: spawned.append(
                            (home, offer, thread, after)) or 4242)
    out = ah.signal_stall(world["proj"], world["thread"], "вопрос висит",
                          offer="12-seam", home=world["home"])
    assert out["autoraise"] == {"after": 1800, "pid": 4242}
    assert spawned == [(world["home"], "12-seam", "seam", 1800)]
    ((_h, text),) = mgr
    assert "через 30 мин tide поднимет сам" in text


def test_signal_stall_autoraise_off_or_no_offer(world, mgr, monkeypatch):
    spawned = []
    monkeypatch.setattr(ah, "_spawn_autoraise", lambda *a: spawned.append(a) or 1)
    monkeypatch.setenv(ah.AUTORAISE_ENV, "off")
    assert ah.signal_stall(world["proj"], world["thread"], "x", offer="12-seam",
                           home=world["home"])["autoraise"]["pid"] is None
    monkeypatch.setenv(ah.AUTORAISE_ENV, "1800")
    assert ah.signal_stall(world["proj"], world["thread"], "x")["autoraise"]["pid"] is None
    assert not spawned


def test_spawn_autoraise_is_detached(monkeypatch, tmp_path):
    seen = {}

    class _P:
        pid = 77

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _P()
    monkeypatch.setattr(ah.subprocess, "Popen", fake_popen)
    assert ah._spawn_autoraise(tmp_path, "12-seam", "seam", 1800) == 77
    assert seen["argv"][1:] == ["-m", "tide", "handoffs", "autoraise", "12-seam",
                                "--after", "1800", "--home", str(tmp_path),
                                "--thread", "seam"]
    assert seen["kw"]["start_new_session"] is True


def test_stalled_auto_handoff_schedules_autoraise_end_to_end(world, monkeypatch, mgr):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, ah.AUTO_CHAIN_FIELD, "3")
    fields.set_field(pp, ah.PROGRESS_MARK_FIELD, "0")
    spawned = []
    monkeypatch.setenv(ah.AUTORAISE_ENV, "1800")
    monkeypatch.setattr(ah, "_spawn_autoraise", lambda home, offer, thread, after:
                        spawned.append((Path(home).resolve(), offer)) or 1)
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: _Fake())
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    (rec,) = hq.list_offers(world["home"])
    assert spawned == [(world["home"].resolve(), rec["name"])]


def test_autoraise_cli_is_wired_and_hidden(world, raise_spy, capsys):
    rec = _offer(world)
    hq.take(world["home"], rec["name"], session="sid-x")
    assert cli.main(["handoffs", "autoraise", rec["name"], "--after", "0",
                     "--home", str(world["home"])]) == 0
    assert '"state": "taken"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["handoffs", "--help"])
    help_lines = capsys.readouterr().out.splitlines()
    # в списке команд с описаниями её нет (в строке usage argparse перечисляет все)
    assert not any(ln.strip().startswith("autoraise") for ln in help_lines)

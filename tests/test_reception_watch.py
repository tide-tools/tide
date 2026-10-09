"""SOC-143: несостоявшийся приём хендоффа не висит молча (дефект 288-04-designer).

09.10 оффер 288-04-designer подняли `tide pickup` в AO app-development-31; агент так и
не стартовал (claude-id пуст, exited), taken-at не встал, а передающая ждала ~2,5 ч:
живость принимающей проверял только автохендофф. Теперь каждый подъём заводит
сторож приёма; не дождался — резерв снят, «не ожила» в raises.log, передающей пульс
и ao send, менеджеру ao send, Грише одно сообщение.

Все адаптеры, время, ao send и сообщения — подделки.
"""

from __future__ import annotations

import pytest

from tide import handoff_queue as hq
from tide.arc import holder
from tide.launcher import autohandoff, handoff, pickup, raise_log, reception, reception_watch

from tests.test_handoff_autospawn import _FakeAo, _use, world  # noqa: F401  fixture

AO_NEW = "proj-7"
SID = "sid-new"


class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Probe(_FakeAo):
    """AO-подделка: *signal* — даёт ли агент первый сигнал; *alive* — жив ли он."""

    def __init__(self, *, signal=True, alive=True):
        super().__init__(ref=AO_NEW)
        self.signal, self.is_alive = signal, alive
        self.resumed = []

    def first_signal(self, handle):
        return self.signal

    def alive(self, handle):
        return self.is_alive if not callable(self.is_alive) else self.is_alive()

    def resume_agent(self, handle):
        self.resumed.append(handle)
        return True


@pytest.fixture
def said(monkeypatch):
    """Перехват всех «сказать»: менеджеру, передающей в AO, Грише."""
    box = {"manager": [], "ao": [], "human": []}
    monkeypatch.setattr(autohandoff, "tell_manager",
                        lambda text: box["manager"].append(text) or {"to": "mgr", "code": 0})
    monkeypatch.setattr(autohandoff, "_ao_send",
                        lambda h, text: box["ao"].append((h, text)) or 0)
    monkeypatch.setattr(reception, "announce_not_alive",
                        lambda home, key: box["human"].append(key) or "sent")
    monkeypatch.setattr(holder, "ao_handle_for",
                        lambda home, sid: "proj-old" if sid == "old-sid" else "")
    return box


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    for mod in (handoff, reception_watch):
        monkeypatch.setattr(mod, "_clock", c.now)
        monkeypatch.setattr(mod, "_sleep", c.sleep)
    monkeypatch.delenv(handoff.LIVE_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(reception_watch.ACCEPT_TIMEOUT_ENV, raising=False)
    return c


@pytest.fixture
def spawns(monkeypatch):
    """Сторож включён, но вместо отсоединённого процесса — запись аргументов."""
    calls = []
    monkeypatch.setenv(reception_watch.WATCH_ENV, "on")
    monkeypatch.setattr(reception_watch, "_spawn_watch",
                        lambda home, key, handle, **kw: calls.append(
                            dict(kw, key=key, handle=handle)) or 4242)
    return calls


def _reserved_offer(home, *, sid=SID):
    path = hq.offer(home, "seam", arc="-", project="proj", seed="-",
                    from_session="old-sid")
    hq.reserve(home, path.stem, session=sid)
    return path.stem


def _hung(world, **kw):
    """Оффер, который реально поднимается дверью (с сид-файлом)."""
    seed = world["proj"] / "seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    return hq.offer(world["home"], "seam", arc="seam", project="proj", seed=str(seed),
                    **kw).stem


def _log(home):
    p = raise_log.log_path(home)
    return p.read_text(encoding="utf-8") if p.exists() else ""


# --- кто заводит сторож -------------------------------------------------------------

def test_pickup_by_hand_schedules_the_watch(world, monkeypatch, spawns):
    _use(monkeypatch, _Probe())
    key = _hung(world)
    out = pickup.run_pickup(world["home"], key)
    assert out["ok"] and out["action"] == "spawned", out
    (call,) = spawns
    assert call["key"] == key and call["handle"] == AO_NEW
    assert call["raised_by"] == raise_log.PICKUP and call["skip_alive"] is False
    rec = hq._resolve(world["home"], key)
    assert call["sid"] == rec["pickup_session"] != "-"


def test_adapter_without_probe_gets_no_watch(world, monkeypatch, spawns):
    _use(monkeypatch, _FakeAo(ref=AO_NEW))  # orca/tmux-подобный: пробы нет
    key = _hung(world)
    assert pickup.run_pickup(world["home"], key)["ok"]
    assert spawns == []


def test_watch_switch_off(world, monkeypatch, spawns):
    monkeypatch.setenv(reception_watch.WATCH_ENV, "off")
    _use(monkeypatch, _Probe())
    key = _hung(world)
    assert pickup.run_pickup(world["home"], key)["ok"]
    assert spawns == []


def test_auto_handoff_watches_only_the_acceptance(world, monkeypatch, spawns, clock):
    _use(monkeypatch, _Probe(signal=True))
    key = _hung(world)
    out = handoff.spawn_offer(world["home"], key)
    assert out["action"] == "spawned"
    (call,) = spawns  # один сторож, не два (дверь не заводит его для auto)
    assert call["raised_by"] == raise_log.AUTO and call["skip_alive"] is True


def test_auto_handoff_not_alive_tells_the_manager_too(world, monkeypatch, said, clock,
                                                      spawns):
    _use(monkeypatch, _Probe(signal=False))
    key = _hung(world, from_session="old-sid")
    out = handoff.spawn_offer(world["home"], key)
    assert out["action"] == handoff.NOT_ALIVE
    assert spawns == []
    assert len(said["manager"]) == 1 and key in said["manager"][0]
    assert said["human"] == [key]
    assert said["ao"] == []  # старая в разговоре — ей печатается строка, ao send не нужен
    assert "не ожила" in _log(world["home"])


# --- сам сторож ---------------------------------------------------------------------

def test_288_never_started_frees_the_offer_and_tells_everyone(world, monkeypatch, said,
                                                              clock):
    """Повтор 288: подняли руками, агент не стартовал — сигнал, а не 2,5 ч тишины."""
    _use(monkeypatch, _Probe(signal=False, alive=False))
    key = _reserved_offer(world["home"])
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID,
                                raised_by=raise_log.PICKUP)
    assert out["action"] == handoff.NOT_ALIVE
    assert clock.t <= 2 * handoff.LIVE_TIMEOUT + handoff.LIVE_POLL  # ~3 мин, не часы
    rec = hq._resolve(world["home"], key)
    assert rec["status"] == hq.STATUS_OFFERED and rec["pickup_session"] == "-"
    assert "pickup · не ожила: AO {0}".format(AO_NEW) in _log(world["home"])
    (mgr,) = said["manager"]
    assert key in mgr and "tide pickup {0}".format(key) in mgr
    ((to, text),) = said["ao"]
    assert to == "proj-old" and "приём не состоялся" in text
    assert said["human"] == [key]


def test_accepted_is_quiet(world, monkeypatch, said, clock):
    _use(monkeypatch, _Probe(signal=True))
    key = _reserved_offer(world["home"])
    hq.confirm_for_session(world["home"], SID)
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID)
    assert out["action"] == "accepted"
    assert said == {"manager": [], "ao": [], "human": []}


def test_died_after_first_signal_is_a_signal(world, monkeypatch, said, clock):
    _use(monkeypatch, _Probe(signal=True, alive=False))
    key = _reserved_offer(world["home"])
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID,
                                raised_by=raise_log.BOARD)
    assert out["action"] == reception_watch.DIED
    assert "умерла" in said["manager"][0]
    assert hq._resolve(world["home"], key)["pickup_session"] == "-"


def test_alive_but_silent_times_out(world, monkeypatch, said, clock):
    _use(monkeypatch, _Probe(signal=True, alive=True))
    key = _reserved_offer(world["home"])
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID, accept_timeout=60)
    assert out["action"] == reception_watch.NOT_ACCEPTED
    assert clock.t >= 60
    assert len(said["manager"]) == 1 and said["human"] == [key]


def test_accepted_while_waiting(world, monkeypatch, said, clock):
    home = world["home"]
    key = _reserved_offer(home)
    probes = []

    def alive():
        probes.append(1)
        if len(probes) == 3:
            hq.confirm_for_session(home, SID)
        return True

    _use(monkeypatch, _Probe(signal=True, alive=alive))
    out = reception_watch.watch(home, key, AO_NEW, sid=SID, skip_alive=True)
    assert out["action"] == "accepted" and said["manager"] == []


def test_superseded_reservation_is_not_ours(world, monkeypatch, said, clock):
    _use(monkeypatch, _Probe(signal=True, alive=False))
    key = _reserved_offer(world["home"], sid="someone-else")
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID, skip_alive=True)
    assert out["action"] == "superseded"
    assert said["manager"] == []
    assert hq._resolve(world["home"], key)["pickup_session"] == "someone-else"


def test_dropped_offer_is_quiet(world, monkeypatch, said, clock):
    _use(monkeypatch, _Probe(signal=False, alive=False))
    key = _reserved_offer(world["home"])
    hq.drop(world["home"], key)
    out = reception_watch.watch(world["home"], key, AO_NEW, sid=SID)
    assert out["action"] in ("accepted", "superseded")
    assert said == {"manager": [], "ao": [], "human": []}

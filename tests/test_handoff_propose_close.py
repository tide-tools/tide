"""Контракт v2 §3 п.4: после приёма tide предлагает закрыть старую — менеджеру.

Не самой старой сессии (она не закрывается сама), а тому, кто закрывает: ``ao
send`` на адрес :func:`tide.whois.manager`. Ровно один раз — метка в оффере.
AO здесь фальшивый: ни одного живого ao send.
"""

from __future__ import annotations

import json

import pytest

from tide import handoff_queue as hq, whois
from tide.launcher import reception

from tests.test_handoff_reception_alert import _pulse, seam, sent  # noqa: F401  fixtures
from tests.test_handoff_autospawn import world  # noqa: F401  fixture


class _Ao:
    def __init__(self, ok=True):
        self.said = []
        self.ok = ok

    def say(self, handle, text):
        self.said.append((handle, text))
        return self.ok


@pytest.fixture
def ao(monkeypatch):
    fake = _Ao()
    monkeypatch.setattr(reception, "_ao_adapter", lambda: fake)
    return fake


@pytest.fixture
def manager(tmp_path, monkeypatch):
    mf = tmp_path / "manager.json"
    mf.write_text(json.dumps({"ao_session": "management-14", "thread": "main",
                              "title": "management — main · 04"}), encoding="utf-8")
    monkeypatch.setenv(whois.MANAGER_FILE_ENV, str(mf))


def test_reception_proposes_close_to_the_manager_once(seam, sent, ao, manager):
    assert _pulse() == 0
    assert ao.said == [("management-14", "proj — seam · 01 передала нить proj — seam · 02, "
                                         "приём подтверждён; закрыть proj — seam · 01?")]
    rec = hq.list_offers(seam["home"])[0]
    mark = hq._get(rec["path"].read_text(encoding="utf-8"), reception.CLOSE_PROPOSED)
    assert mark.endswith("· management-14 · sent")
    assert _pulse("дальше") == 0
    assert reception.propose_close(seam["home"], seam["key"]) is None
    assert len(ao.said) == 1


def test_no_manager_address_sends_nothing(seam, sent, ao):
    # no manager.json (conftest) and no management project in the roster
    assert _pulse() == 0
    assert ao.said == []
    rec = hq.list_offers(seam["home"])[0]
    assert hq._get(rec["path"].read_text(encoding="utf-8"),
                   reception.CLOSE_PROPOSED).endswith("no-manager")


def test_not_taken_offer_proposes_nothing(seam, ao, manager):
    assert reception.propose_close(seam["home"], seam["key"]) is None
    assert ao.said == []


def test_close_proposal_ignores_the_notify_switch(seam, sent, ao, manager, monkeypatch):
    monkeypatch.setenv(reception.NOTIFY_ENV, "off")
    assert _pulse() == 0
    assert sent == []                      # Грише — ничего
    assert len(ao.said) == 1               # менеджеру — всё равно

"""SOC-98: сессия, отдавшая нить, растёт дальше без пингов (designer · 03 дошёл
до 362 тыс.). Теперь контекст считается и у неё: на 300 тыс. — пинг «доведи
воркера, перешли итог, замолчи», на 500 — плюс тревога менеджеру «закрой её».
Один пинг на ступень (штамп ``ctx-ping``), ступень 200 ей не нужна.
"""

from __future__ import annotations

import pytest

from tide import fields
from tide.hooks import context_watch as cw
from tide.launcher import autohandoff as ah

from tests.test_autohandoff import _payload, world  # noqa: F401  fixture
from tests.test_context_watch_handed import _hand_over


@pytest.fixture
def sent(monkeypatch):
    calls = {"alert": [], "manager": []}

    def fake_alert(text, *, route, source="", extra_env=None):
        calls["alert"].append((route, text))
        return None  # нет $TIDE_ALERT_CMD — тогда ao send менеджеру

    def fake_tell(text):
        calls["manager"].append(text)
        return {"to": "mgr-1", "code": 0}

    monkeypatch.setattr(ah, "send_alert", fake_alert)
    monkeypatch.setattr(ah, "tell_manager", fake_tell)
    return calls


def _handed_off(world):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, "title", "proj — designer · 03 · proj-10")
    fields.set_field(pp, cw.HANDED_OFF_FIELD, "2026-10-09T06:35:45 → 01-x")


def test_handed_off_pings_only_on_300_and_500(world, sent):
    _handed_off(world)
    assert cw.watch_reason(world["proj"], _payload(world, 250_000)) is None
    assert sent == {"alert": [], "manager": []}

    r300 = cw.watch_reason(world["proj"], _payload(world, 320_000))
    assert r300 == ("tide: ты передала нить, контекст 320 тыс.: доведи воркера, "
                    "перешли итог новой и замолчи.")
    assert cw.AUTO_CMD not in r300
    assert [route for route, _ in sent["alert"]] == [cw.ROUTE_LOG]
    assert sent["manager"] == []
    # та же ступень второй раз не пингуется
    assert cw.watch_reason(world["proj"], _payload(world, 340_000)) is None

    r500 = cw.watch_reason(world["proj"], _payload(world, 520_000))
    assert "контекст 520 тыс." in r500
    assert sent["alert"][-1][0] == cw.ROUTE_MANAGER
    assert sent["manager"] == ["сессия designer · 03 · proj-10 передала нить в 06:35, "
                               "контекст 520 тыс. — закрой её"]
    assert cw.watch_reason(world["proj"], _payload(world, 600_000)) is None
    assert len(sent["manager"]) == 1
    assert (fields.read_field(world["sess"] / "arc.md", cw.CTX_PING_FIELD) or "")\
        .startswith("500 ")


def test_alert_cmd_present_no_double_send(world, monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "send_alert",
                        lambda text, *, route, source="", extra_env=None:
                        calls.append(route) or 0)
    monkeypatch.setattr(ah, "tell_manager", lambda text: calls.append("ao"))
    _handed_off(world)
    assert cw.watch_reason(world["proj"], _payload(world, 520_000))
    assert calls == [cw.ROUTE_MANAGER]


def test_handed_over_notice_and_ping_in_one_block(world, sent):
    _hand_over(world)
    r = cw.watch_reason(world["proj"], _payload(world, 320_000))
    assert r.count("\n") == 1
    assert r.startswith("tide: нить передана")
    assert r.endswith("перешли итог новой и замолчи.")
    # напоминание уже сказано, ступень пропингована — тишина
    assert cw.watch_reason(world["proj"], _payload(world, 330_000)) is None

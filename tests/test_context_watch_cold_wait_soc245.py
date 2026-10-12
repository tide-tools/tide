"""SOC-245: холодное пробуждение не требует хендоффа у сессии, которая законно ждёт.

Живой случай 12.10: техлид 30 и 31 ждали письма/ревью («мяч у меня: SOC-N, жду … к
HH:MM»), простаивали 1–2 ч и на первом ходе после простоя получали требование
передачи — две лишние передачи за ночь. Теперь курсор по правилу мяча с открытым
сроком (срок не прошёл или прошёл меньше ``$TIDE_CTX_COLD_WAIT_GRACE_MIN`` минут,
30) — пробуждение плановое: хендофф не требуем, в паспорте штамп ``cold-wait``.
Срок давно прошёл, курсор не «мяч у меня» или нарушает правило мяча — как раньше.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tide import fields, offload
from tide.hooks import context_watch as cw

from tests.test_autohandoff import world  # noqa: F401  fixture
from tests.test_context_watch_cold_wake import _wake_payload, sent  # noqa: F401  fixture

# в _wake_payload прошлый ход кончился в 08:00:02Z, пробуждение — 09:30:00Z (89 мин)


def _hm(utc: str) -> str:
    """HH:MM местного времени для момента *utc* (курсор пишут по местным часам)."""
    at = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    return at.astimezone().strftime("%H:%M")


@pytest.fixture(autouse=True)
def _grace(monkeypatch):
    monkeypatch.delenv(cw.COLD_WAIT_GRACE_ENV, raising=False)


def _cursor(world, text: str, eta=None) -> None:
    offload.offload(world["proj"], "seam/s1", cursor=text, eta=eta)


def test_open_wait_before_deadline_skips_cold_handoff(world, sent):
    _cursor(world, "мяч у меня: SOC-150, жду итог выката от music main к {0}".format(
        _hm("2026-10-12T09:45:00Z")))
    pp = world["sess"] / "arc.md"
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000)) is None
    assert sent == []
    assert not (fields.read_field(pp, cw.COLD_PING_FIELD) or "").strip()
    stamp = fields.read_field(pp, cw.COLD_WAIT_FIELD) or ""
    assert stamp.startswith("2026-10-12T09:30:00 89 110000 ")
    assert _hm("2026-10-12T09:45:00Z") in stamp


def test_deadline_passed_recently_is_still_open(world, sent):
    _cursor(world, "мяч у меня: SOC-245, жду помощника к {0}".format(
        _hm("2026-10-12T09:10:00Z")))
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000)) is None
    assert sent == []


def test_latest_of_several_deadlines_counts(world, sent):
    _cursor(world, "мяч у меня: SOC-150 итог выката к {0}; SOC-245 жду помощника к {1}"
            .format(_hm("2026-10-12T08:20:00Z"), _hm("2026-10-12T10:00:00Z")))
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000)) is None


def test_eta_field_is_the_deadline_without_hhmm(world, sent):
    _cursor(world, "мяч у меня: SOC-150, жду письмо music main",
            eta="2026-10-12T10:00:00+00:00")
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000)) is None


def test_long_overdue_wait_still_hands_off(world, sent):
    _cursor(world, "мяч у меня: SOC-150, жду итог выката к {0}".format(
        _hm("2026-10-12T08:30:00Z")))
    r = cw.watch_reason(world["proj"], _wake_payload(world, 110_000))
    assert r and "проснулась после простоя 1 ч 29 мин" in r


def test_grace_is_tunable(world, sent, monkeypatch):
    monkeypatch.setenv(cw.COLD_WAIT_GRACE_ENV, "90")
    _cursor(world, "мяч у меня: SOC-150, жду итог выката к {0}".format(
        _hm("2026-10-12T08:30:00Z")))
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000)) is None


@pytest.mark.parametrize("cursor", [
    "жду ответа",                                  # нарушение правила мяча
    "мяч у меня: жду письмо к 23:59",              # без задачи — нарушение
    "делаю SOC-150, сверка к 23:59",               # не ожидание
])
def test_not_a_legit_wait_hands_off_as_before(world, sent, cursor):
    _cursor(world, cursor)
    assert cw.watch_reason(world["proj"], _wake_payload(world, 110_000))


def test_wait_deadline_across_midnight(world, sent):
    """Курсор «к 00:10», сессия уснула в 23:50 местного — срок завтрашний, не вчерашний."""
    asleep = datetime(2026, 10, 12, 23, 50)
    assert cw.resolve_hhmm("00:10", asleep) == datetime(2026, 10, 13, 0, 10)
    assert cw.resolve_hhmm("23:30", asleep) == datetime(2026, 10, 12, 23, 30)
    assert cw.resolve_hhmm("08:00", datetime(2026, 10, 12, 7, 30)) == \
        datetime(2026, 10, 12, 8, 0)

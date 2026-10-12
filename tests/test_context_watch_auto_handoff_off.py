"""SOC-230: ``auto-handoff: off`` в паспорте — сторож контекста молчит на любом
пороге: Stop не блокируется, тревоги менеджеру нет, ``ctx-ping`` не штампуется.
"""

from __future__ import annotations

import pytest

from tide import fields
from tide.hooks import context_watch as cw
from tide.launcher import autohandoff as ah

from tests.test_autohandoff import _payload, world  # noqa: F401  fixture


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "send_alert",
                        lambda text, *, route, source="", extra_env=None: calls.append(text))
    monkeypatch.setattr(ah, "tell_manager", lambda text: calls.append(text))
    return calls


@pytest.mark.parametrize("tokens", [260_000, 320_000, 520_000])
def test_off_silences_every_threshold(world, sent, tokens):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, "auto-handoff", "off")
    assert cw.watch_reason(world["proj"], _payload(world, tokens)) is None
    assert sent == []
    assert not (fields.read_field(pp, cw.CTX_PING_FIELD) or "").strip()


def test_without_field_still_blocks(world, sent):
    assert cw.watch_reason(world["proj"], _payload(world, 320_000)) is not None

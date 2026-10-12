"""SOC-237: передача раньше — порог 150 тыс. и холодное пробуждение.

* порог передачи автономной сессии — 150 тыс. (потолок 200, тревога 300);
* простой перед ходом > 60 мин при контексте > 60 тыс. — на первом ходе после
  простоя та же передача, что на пороге; одно пробуждение — один пинг (``cold-ping``);
* ``auto-handoff: off`` (разговор, SOC-230) глушит и порог, и холодное пробуждение.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import fields
from tide.hooks import context_watch as cw
from tide.launcher import autohandoff as ah

from tests.test_autohandoff import SID, _payload, world  # noqa: F401  fixture


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "send_alert",
                        lambda text, *, route, source="", extra_env=None:
                        calls.append((route, text)))
    monkeypatch.setattr(ah, "tell_manager", lambda text: calls.append(("ao", text)))
    for k in (cw.COLD_MIN_ENV, cw.COLD_TOKENS_ENV):
        monkeypatch.delenv(k, raising=False)
    return calls


def _asst(at: str, tokens: int, mid: str) -> dict:
    return {"type": "assistant", "timestamp": at,
            "message": {"id": mid, "model": "claude-opus-5-5",
                        "content": [{"type": "text", "text": "ок"}],
                        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": tokens - 10}}}


def _user(at: str, text: str = "дальше") -> dict:
    return {"type": "user", "timestamp": at, "message": {"role": "user", "content": text}}


def _stop(at: str) -> dict:
    return {"type": "system", "subtype": "stop_hook_summary", "timestamp": at}


def _queue(at: str) -> dict:
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": at,
            "content": "письмо"}


def _wake_payload(world, tokens: int, *, slept_until: str = "2026-10-12T09:30:00Z",
                  extra_before=()) -> dict:
    """Прошлый ход кончился в 08:00 (Stop-хук), следующий — после простоя."""
    rows = [
        _user("2026-10-12T07:59:00Z", "сделай"),
        _asst("2026-10-12T08:00:00Z", tokens - 5_000, "m1"),
        _stop("2026-10-12T08:00:02Z"),
        *extra_before,
        _user(slept_until),
        {"type": "assistant", "timestamp": slept_until.replace(":00Z", ":20Z"),
         "message": {"id": "m2", "model": "claude-opus-5-5",
                     "content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                                  "input": {"command": "ls"}}],
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": tokens - 20}}},
        {"type": "user", "timestamp": slept_until.replace(":00Z", ":21Z"),
         "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                                  "content": "ok"}]}},
        _asst(slept_until.replace(":00Z", ":40Z"), tokens, "m3"),
    ]
    t = world["tmp"] / "tx" / "{0}.jsonl".format(SID)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")
    return {"session_id": SID, "transcript_path": str(t), "stop_hook_active": False}


# --- порог 150 ------------------------------------------------------------------

def test_handoff_threshold_is_150k(world, sent):
    assert cw.thresholds() == (150_000, 200_000, 300_000)
    assert cw.watch_reason(world["proj"], _payload(world, 149_000)) is None
    r = cw.watch_reason(world["proj"], _payload(world, 150_000))
    assert r and "порог передачи 150 тыс." in r and cw.AUTO_CMD in r
    assert "200 тыс. — потолок" in r


# --- холодное пробуждение -------------------------------------------------------

def test_idle_before_turn_measures_the_gap_since_last_stop(world):
    p = _wake_payload(world, 80_000)
    idle, woke = cw.idle_before_turn(Path(p["transcript_path"]))
    assert int(idle) == 89 and woke.isoformat() == "2026-10-12T09:30:00"


def test_cold_wake_pings_handoff_once(world, sent):
    pp = world["sess"] / "arc.md"
    p = _wake_payload(world, 80_000)
    r = cw.watch_reason(world["proj"], p)
    assert r and "проснулась после простоя 1 ч 29 мин" in r and "80 тыс." in r
    assert "tide offload seam/s1" in r and cw.AUTO_CMD in r
    assert fields.read_field(pp, cw.COLD_PING_FIELD).startswith("2026-10-12T09:30:00 89 ")
    assert [route for route, _ in sent] == [cw.ROUTE_LOG]
    # тот же ход / то же пробуждение — второй раз не пингуем
    assert cw.watch_reason(world["proj"], p) is None
    assert len(sent) == 1
    # ступени лестницы не тронуты
    assert not (fields.read_field(pp, cw.CTX_PING_FIELD) or "").strip()


def test_short_idle_is_silent(world, sent):
    p = _wake_payload(world, 80_000, slept_until="2026-10-12T08:55:00Z")
    assert cw.watch_reason(world["proj"], p) is None
    assert sent == []


def test_small_context_is_silent(world, sent):
    p = _wake_payload(world, 50_000)
    assert cw.watch_reason(world["proj"], p) is None
    assert sent == []


def test_queue_stamp_before_the_prompt_does_not_hide_the_idle(world, sent):
    """Письмо в очереди штампуется в момент прихода — это не ход сессии."""
    p = _wake_payload(world, 80_000, extra_before=[_queue("2026-10-12T09:29:59Z")])
    assert "проснулась после простоя" in cw.watch_reason(world["proj"], p)


def test_idle_of_an_earlier_turn_does_not_count(world, sent):
    p = _wake_payload(world, 80_000)
    path = Path(p["transcript_path"])
    rows = path.read_text(encoding="utf-8")
    more = [_stop("2026-10-12T09:31:00Z"), _user("2026-10-12T09:35:00Z"),
            _asst("2026-10-12T09:36:00Z", 81_000, "m4")]
    path.write_text(rows + "".join(json.dumps(r) + "\n" for r in more), encoding="utf-8")
    assert cw.watch_reason(world["proj"], p) is None


def test_ladder_ping_wins_over_cold_wake(world, sent):
    r = cw.watch_reason(world["proj"], _wake_payload(world, 210_000))
    assert r and "жёсткий порог 200 тыс." in r and "проснулась" not in r


def test_cold_wake_thresholds_from_env(world, sent, monkeypatch):
    monkeypatch.setenv(cw.COLD_MIN_ENV, "120")
    assert cw.watch_reason(world["proj"], _wake_payload(world, 80_000)) is None
    monkeypatch.setenv(cw.COLD_MIN_ENV, "60")
    monkeypatch.setenv(cw.COLD_TOKENS_ENV, "100000")
    assert cw.watch_reason(world["proj"], _wake_payload(world, 80_000)) is None


def test_handed_off_session_gets_no_cold_wake(world, sent):
    fields.set_field(world["sess"] / "arc.md", ah.HANDED_OFF_FIELD, "2026-10-12 → 01-x")
    assert cw.watch_reason(world["proj"], _wake_payload(world, 80_000)) is None


# --- auto-handoff: off глушит оба условия -----------------------------------------

@pytest.mark.parametrize("tokens", [80_000, 160_000, 210_000, 320_000])
def test_off_silences_threshold_and_cold_wake(world, sent, tokens):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, "auto-handoff", "off")
    assert cw.watch_reason(world["proj"], _wake_payload(world, tokens)) is None
    assert cw.watch_reason(world["proj"], _payload(world, tokens)) is None
    assert sent == []
    assert not (fields.read_field(pp, cw.CTX_PING_FIELD) or "").strip()
    assert not (fields.read_field(pp, cw.COLD_PING_FIELD) or "").strip()

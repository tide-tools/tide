"""SOC-237: два класса сессий — исполнитель и менеджер.

Исполнитель: 150 спокойный момент / 200 обязательно / 300 тревога (см. остальные тесты).
Менеджер (оркестратор: менеджер, лиды, main проектов; ``auto-handoff: off``): с 200 тыс.
ПРЕДЛОЖИТЬ хендофф пользователю (Stop не блокируется), повтор каждые +50 тыс.; с 350 тыс. —
принудительный хендофф; холодное пробуждение — только предложение.
"""

from __future__ import annotations

import io
import json

import pytest

from tide import fields, handoff_contract as hc
from tide.hooks import handed_off
from tide.arc import stream
from tide.hooks import context_watch as cw
from tide.launcher import autohandoff as ah

from tests.test_autohandoff import SID, _payload, world  # noqa: F401  fixture
from tests.test_context_watch_cold_wake import _wake_payload


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(ah, "send_alert",
                        lambda text, *, route, source="", extra_env=None:
                        calls.append((route, text)))
    monkeypatch.setattr(ah, "tell_manager", lambda text: calls.append(("ao", text)))
    for k in (cw.COLD_MIN_ENV, cw.COLD_TOKENS_ENV, cw.MGR_SUGGEST_ENV, cw.MGR_FORCE_ENV):
        monkeypatch.delenv(k, raising=False)
    return calls


@pytest.fixture
def manager(world):
    fields.set_field(world["sess"] / "arc.md", "auto-handoff", "off")
    return world


def _stamp(world):
    return (fields.read_field(world["sess"] / "arc.md", cw.CTX_PING_FIELD) or "").strip()


# --- константы и класс ----------------------------------------------------------

def test_constants_next_to_handoff_tokens():
    assert hc.HANDOFF_TOKENS == 150_000
    assert (hc.HANDOFF_TOKENS_MANAGER_SUGGEST, hc.HANDOFF_TOKENS_MANAGER_STEP,
            hc.HANDOFF_TOKENS_MANAGER_FORCE) == (200_000, 50_000, 350_000)
    assert cw.manager_thresholds() == (200_000, 50_000, 350_000)
    assert cw.thresholds() == (150_000, 200_000, 300_000)


def test_class_default_is_worker(world):
    assert cw.session_class(world["sess"]) == hc.CLASS_WORKER


def test_class_auto_handoff_off_is_manager(world):
    for v in ("off", "OFF", "no", "false", "0"):
        fields.set_field(world["sess"] / "arc.md", "auto-handoff", v)
        assert cw.session_class(world["sess"]) == hc.CLASS_MANAGER


def test_class_explicit_field_wins(world):
    pp = world["sess"] / "arc.md"
    fields.set_field(pp, "session-class", "manager")
    assert cw.session_class(world["sess"]) == hc.CLASS_MANAGER
    fields.set_field(pp, "auto-handoff", "off")
    fields.set_field(pp, "session-class", "worker")
    assert cw.session_class(world["sess"]) == hc.CLASS_WORKER


def test_class_by_thread_slug(world):
    stream.new_thread(world["proj"], "main", goal="вести проект")
    sess = stream.new_session(world["proj"], "main", "m1", claude_session="mgr-sid")
    assert cw.session_class(sess) == hc.CLASS_MANAGER


def test_class_ignores_tide_role_env(world, monkeypatch):
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    assert cw.session_class(world["sess"]) == hc.CLASS_WORKER


# --- лестница менеджера ---------------------------------------------------------

def test_manager_below_suggest_is_silent(manager, sent):
    assert cw.watch(manager["proj"], _payload(manager, 199_000)) == (None, None)
    assert sent == [] and _stamp(manager) == ""


@pytest.mark.parametrize("tokens", [200_000, 240_000, 300_000])
def test_manager_suggest_does_not_block(manager, sent, tokens):
    block, hint = cw.watch(manager["proj"], _payload(manager, tokens))
    assert block is None
    assert hint == "tide: предложи пользователю хендофф: контекст {0} тыс.".format(
        tokens // 1000)
    assert cw.watch_reason(manager["proj"], _payload(manager, tokens)) is None
    pp = manager["sess"] / "arc.md"
    assert (fields.read_field(pp, cw.SUGGEST_FIELD) or "").startswith("ctx ")
    assert [r for r, _ in sent] == [cw.ROUTE_LOG]


def test_manager_suggest_repeats_every_50k(manager, sent):
    proj = manager["proj"]
    assert cw.watch(proj, _payload(manager, 200_000))[1]
    assert _stamp(manager).startswith("200 ")
    assert cw.watch(proj, _payload(manager, 220_000)) == (None, None)  # та же ступень
    assert cw.watch(proj, _payload(manager, 249_000)) == (None, None)
    assert "251 тыс." in cw.watch(proj, _payload(manager, 251_000))[1]
    assert _stamp(manager).startswith("250 ")
    assert "300 тыс." in cw.watch(proj, _payload(manager, 300_000))[1]


def test_manager_force_blocks_at_350k(manager, sent):
    block, hint = cw.watch(manager["proj"], _payload(manager, 350_000))
    assert hint is None
    assert block and "принудительный порог менеджера 350 тыс." in block
    assert cw.AUTO_CMD in block and "tide offload seam/s1" in block
    assert _stamp(manager).startswith("350 ")
    # тот же уровень — не пингуем повторно; +50 тыс. — снова требуем
    assert cw.watch(manager["proj"], _payload(manager, 360_000)) == (None, None)
    assert cw.watch(manager["proj"], _payload(manager, 400_000))[0]
    # менеджеру тревоги «менеджеру» нет — только журнал
    assert all(r == cw.ROUTE_LOG for r, _ in sent)


def test_manager_force_clears_pending_hint(manager, sent):
    cw.watch(manager["proj"], _payload(manager, 300_000))
    assert fields.read_field(manager["sess"] / "arc.md", cw.SUGGEST_FIELD)
    assert cw.watch(manager["proj"], _payload(manager, 350_000))[0]
    assert not fields.read_field(manager["sess"] / "arc.md", cw.SUGGEST_FIELD)


def test_manager_thresholds_from_env(manager, sent, monkeypatch):
    monkeypatch.setenv(cw.MGR_SUGGEST_ENV, "100000")
    monkeypatch.setenv(cw.MGR_FORCE_ENV, "120000")
    assert cw.watch(manager["proj"], _payload(manager, 100_000))[1]
    assert cw.watch(manager["proj"], _payload(manager, 120_000))[0]


def test_manager_stop_hook_active_is_silent(manager, sent):
    assert cw.watch(manager["proj"], _payload(manager, 400_000, stop_hook_active=True)) == \
        (None, None)


def test_manager_150k_is_not_a_threshold(manager, sent):
    assert cw.watch(manager["proj"], _payload(manager, 160_000)) == (None, None)


# --- холодное пробуждение менеджера: только предложение ---------------------------

def test_manager_cold_wake_is_a_suggestion(manager, sent):
    pp = manager["sess"] / "arc.md"
    block, hint = cw.watch(manager["proj"], _wake_payload(manager, 80_000))
    assert block is None
    assert hint and "предложи пользователю хендофф" in hint and "1 ч 29 мин" in hint
    assert fields.read_field(pp, cw.COLD_PING_FIELD).startswith("2026-10-12T09:30:00 89 ")
    assert (fields.read_field(pp, cw.SUGGEST_FIELD) or "").startswith("cold ")
    # то же пробуждение — второй раз не предлагаем
    assert cw.watch(manager["proj"], _wake_payload(manager, 80_000)) == (None, None)


def test_manager_cold_wake_small_context_is_silent(manager, sent):
    assert cw.watch(manager["proj"], _wake_payload(manager, 50_000)) == (None, None)


# --- подсказка доходит до агента: UserPromptSubmit ----------------------------------

def test_prompt_hint_delivers_once(manager, sent):
    cw.watch(manager["proj"], _payload(manager, 260_000))
    line = cw.prompt_hint(manager["proj"], SID)
    assert line == "tide: предложи пользователю хендофф: контекст 260 тыс."
    assert cw.prompt_hint(manager["proj"], SID) is None


def test_prompt_hint_for_cold_wake(manager, sent):
    cw.watch(manager["proj"], _wake_payload(manager, 80_000))
    assert "проснулась после простоя 1 ч 29 мин" in cw.prompt_hint(manager["proj"], SID)


def test_prompt_hint_silent_for_worker(world, sent):
    assert cw.prompt_hint(world["proj"], SID) is None


def test_prompt_hint_silent_after_handoff(manager, sent):
    cw.watch(manager["proj"], _payload(manager, 260_000))
    fields.set_field(manager["sess"] / "arc.md", ah.HANDED_OFF_FIELD, "2026-10-12 → 01-x")
    assert cw.prompt_hint(manager["proj"], SID) is None


def test_prompt_hook_prints_hint(manager, sent, monkeypatch, capsys):
    cw.watch(manager["proj"], _payload(manager, 260_000))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": SID, "prompt": "привет"})))
    assert handed_off.cmd_prompt(None) == 0
    assert "предложи пользователю хендофф: контекст 260 тыс." in capsys.readouterr().out


def test_stop_cmd_prints_system_message_without_block(manager, sent, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload(manager, 260_000))))
    assert cw.cmd_context_watch(None) == 0
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "предложи пользователю хендофф" in out["systemMessage"]


def test_stop_cmd_blocks_on_force(manager, sent, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload(manager, 360_000))))
    assert cw.cmd_context_watch(None) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


# --- отдавший нить менеджер -----------------------------------------------------------

def test_handed_off_manager_pinged_only_at_force(manager, sent):
    fields.set_field(manager["sess"] / "arc.md", ah.HANDED_OFF_FIELD, "2026-10-12 → 01-x")
    assert cw.watch(manager["proj"], _payload(manager, 220_000)) == (None, None)
    assert cw.watch(manager["proj"], _payload(manager, 330_000)) == (None, None)
    block, _ = cw.watch(manager["proj"], _payload(manager, 360_000))
    assert block and "передала нить" in block
    assert all(r != cw.ROUTE_MANAGER for r, _ in sent)


# --- исполнитель не изменился -----------------------------------------------------------

def test_worker_ladder_unchanged(world, sent):
    assert cw.watch_reason(world["proj"], _payload(world, 149_000)) is None
    assert "порог передачи 150 тыс." in cw.watch_reason(world["proj"], _payload(world, 150_000))
    assert "жёсткий порог 200 тыс." in cw.watch_reason(world["proj"], _payload(world, 200_000))
    assert "ТРЕВОГА" in cw.watch_reason(world["proj"], _payload(world, 300_000))
    assert not fields.read_field(world["sess"] / "arc.md", cw.SUGGEST_FIELD)

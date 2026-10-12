"""SOC-232: в AO-сессию менеджера ничего не впечатывается автоматически."""

from __future__ import annotations

import json
import sys

import pytest

from tide import manager_guard
from tide.hooks import role_gate
from tide.launcher import autohandoff as ah


@pytest.fixture
def fake_ao(tmp_path, monkeypatch):
    sink = tmp_path / "argv.txt"
    fake = tmp_path / "ao"
    fake.write_text("#!{0}\nimport sys; open({1!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                    .format(sys.executable, str(sink)), encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("TIDE_AO_BIN", str(fake))
    monkeypatch.setattr(manager_guard, "fallback_home", lambda: tmp_path / "home")
    monkeypatch.setattr("tide.paths.find_tide_root", lambda *a, **k: None)
    return sink


@pytest.fixture(autouse=True)
def _mgr37(monkeypatch):
    from tide import whois

    monkeypatch.setattr(whois, "manager", lambda **kw: {"ao_session": "management-37"})
    monkeypatch.delenv("AO_SESSION_ID", raising=False)


def test_ao_send_to_manager_is_suppressed_and_logged(fake_ao, tmp_path):
    assert ah._ao_send("management-37", "нить встала") == 0
    assert not fake_ao.exists()                       # ao не вызывали
    log = tmp_path / "home" / manager_guard.FALLBACK_LOG
    ev = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert ev["type"] == "suppressed-to-manager" and ev["text"] == "нить встала"
    assert ev["to"] == "management-37"


def test_ao_send_to_regular_role_goes_out(fake_ao):
    assert ah._ao_send("music-32", "привет") == 0
    assert fake_ao.read_text(encoding="utf-8").strip() == "send --session music-32 --message привет"


def test_tell_manager_by_whois_address_is_suppressed(fake_ao, monkeypatch):
    from tide import whois

    monkeypatch.setattr(whois, "manager", lambda **kw: {"ao_session": "weird-1"})
    assert ah.tell_manager("тревога")["code"] == 0
    assert not fake_ao.exists()


def test_adapter_say_to_manager_is_suppressed(fake_ao):
    from tide.adapters.ao import AoAdapter

    assert AoAdapter().say("management-37", "x") is False
    assert not fake_ao.exists()


def _bash(cmd):
    return role_gate.manager_window_refusal("Bash", {"command": cmd})


def test_role_gate_blocks_ao_send_to_manager():
    assert _bash('ao send --session management-37 --message "x"') == role_gate.MANAGER_WINDOW_REFUSAL
    assert _bash('echo 1 && ao send --session=management-37 --message x')
    assert "SOC-232" in role_gate.MANAGER_WINDOW_REFUSAL


def test_role_gate_passes_ao_send_to_others():
    assert _bash('ao send --session music-32 --message "x"') is None
    assert _bash("ls -la") is None


def test_role_gate_sendmessage_to_manager():
    assert role_gate.manager_window_refusal("SendMessage", {"to": "management-37"})
    assert role_gate.manager_window_refusal("SendMessage", {"to": "soc223"}) is None


def test_cmd_role_gate_blocks_before_decide(monkeypatch, capsys):
    import io

    payload = {"tool_name": "Bash", "agent_id": "sub-1",
               "tool_input": {"command": 'ao send --session management-37 --message "x"'}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert role_gate.cmd_role_gate(type("A", (), {"explain": None, "command": None,
                                                  "file": None})()) == role_gate.BLOCK_EXIT


def test_other_management_thread_is_not_manager(fake_ao):
    assert not manager_guard.is_manager_session("management-40")
    assert ah._ao_send("management-40", "пинг") == 0
    assert "management-40" in fake_ao.read_text(encoding="utf-8")
    assert _bash('ao send --session management-40 --message "x"') is None


def test_sendmessage_name_with_suffix_is_manager():
    assert role_gate.manager_window_refusal("SendMessage", {"to": "management-37-4b"})
    assert role_gate.manager_window_refusal("SendMessage", {"to": "management-40"}) is None


def test_whois_unavailable_falls_back_to_prefix(monkeypatch):
    from tide import whois

    monkeypatch.setattr(whois, "manager", lambda **kw: {})
    assert manager_guard.is_manager_session("management-40")
    def boom(**kw):
        raise RuntimeError
    monkeypatch.setattr(whois, "manager", boom)
    assert manager_guard.is_manager_session("management-40")


def test_manager_itself_is_not_blocked(monkeypatch):
    monkeypatch.setenv("AO_SESSION_ID", "management-37")
    assert _bash('ao send --session management-37 --message "x"') is None

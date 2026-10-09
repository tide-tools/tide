"""SOC-101 C14: почта старой сессии после приёма доходит до новой механикой.

Старая, уже отдавшая нить (её оффер принят), на входящем сообщении другой сессии
(``ao send`` / uds) пересылает его новой через ``ao send`` (здесь подменён —
наружу ничего не уходит) и печатает в свой контекст, кому передана нить и что
сказать отправителю. Слова человека, сообщения самой новой и менеджера, а также
сессии, нить не отдававшие, — без пересылки.
"""

from __future__ import annotations

import io
import json

import pytest

from tide import cli, fields, handoff_queue as hq
from tide.arc import stream
from tide.hooks import handed_off

from tests.test_handoff_autospawn import world  # noqa: F401  fixture


AO_MSG = ('<ao-session-delivery id="session-send:abc-1">\n[from tide-stack-17] '
          'board · 02 → main · 03: SOC-100 в In Review, пруф в комментарии\n'
          '</ao-session-delivery>')
UDS_MSG = ('Another Claude session sent a message:\n<cross-session-message '
           'from="uds:/tmp/cc-socks/1.sock" from-name="tide-stack-20-9e" '
           'from-mode="bypass">\ncontinuity-audit · 02 → main · 03: промер готов\n'
           '</cross-session-message>')


class _Ao:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def say(self, handle, text):
        self.calls.append((handle, text))
        return self.ok


@pytest.fixture
def handed(world, monkeypatch, tmp_path):
    proj, home = world["proj"], world["home"]
    old = stream.new_session(proj, "seam", "old-work", goal="old work")
    fields.set_field(old / "arc.md", "claude-session", "old-sid")
    fields.set_field(old / "arc.md", "title", "proj — seam · 01")
    new = stream.new_session(proj, "seam", "seam-2", goal="шов")
    fields.set_field(new / "arc.md", "title", "proj — seam · 02 · proj-24")
    seed = new / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    hq.offer(home, "seam", arc="01-@seam/" + new.name, project="proj", seed=str(seed),
             from_session="old-sid")
    key = hq.list_offers(home)[0]["name"]
    hq.reserve(home, key, session="new-sid")
    hq.take(home, key, session="new-sid")
    mgr = tmp_path / "manager.json"
    mgr.write_text(json.dumps({"ao_session": "management-14"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mgr))
    monkeypatch.setenv("AO_SESSION_ID", "proj-19")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv(handed_off.FORWARD_ENV, raising=False)
    ao = _Ao()
    monkeypatch.setattr(handed_off, "_ao_adapter", lambda: ao)
    return {"home": home, "ao": ao, "key": key}


def _hook(monkeypatch, prompt, sid="old-sid"):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": sid, "prompt": prompt})))
    return cli.main(["hook", "handoff-confirm"])


def test_incoming_parses_both_wrappers_and_skips_human():
    ao = handed_off.incoming(AO_MSG)
    assert ao["sender"] == "tide-stack-17" and ao["id"] == "session-send:abc-1"
    assert ao["body"].startswith("board · 02 → main · 03: SOC-100")
    uds = handed_off.incoming(UDS_MSG)
    assert uds["sender"] == "tide-stack-20-9e" and uds["body"].endswith("промер готов")
    assert uds["id"] == handed_off.incoming(UDS_MSG)["id"]   # стабилен между процессами
    assert handed_off.incoming("Гриша: сделай доску") is None


def test_old_session_forwards_to_new_and_tells_context(handed, monkeypatch, capsys):
    assert _hook(monkeypatch, AO_MSG) == 0
    out = capsys.readouterr().out
    assert handed["ao"].calls == [("proj-24", handed["ao"].calls[0][1])]
    text = handed["ao"].calls[0][1]
    assert text.startswith("[переслано от старой proj — seam · 01 · proj-19, "
                           "отправитель tide-stack-17] board · 02 → main · 03")
    assert "нить передана proj — seam · 02 · proj-24" in out
    assert "уже переслано ей механикой" in out
    assert "Скажи отправителю tide-stack-17 новый адрес: proj — seam · 02 · proj-24" in out
    # та же доставка второй раз — без второй пересылки
    assert _hook(monkeypatch, AO_MSG) == 0
    assert len(handed["ao"].calls) == 1
    assert "уже переслано" in capsys.readouterr().out


def test_uds_message_is_forwarded_too(handed, monkeypatch, capsys):
    assert _hook(monkeypatch, UDS_MSG) == 0
    assert handed["ao"].calls[0][0] == "proj-24"
    assert "отправитель tide-stack-20-9e" in handed["ao"].calls[0][1]


def test_failed_send_asks_to_forward_by_hand(handed, monkeypatch, capsys):
    handed["ao"].ok = False
    assert _hook(monkeypatch, AO_MSG) == 0
    out = capsys.readouterr().out
    assert "Перешли это сообщение ей (ao send --session proj-24 --message …)" in out
    handed["ao"].ok = True
    assert _hook(monkeypatch, AO_MSG) == 0   # не запомнено — повтор пробует снова
    assert len(handed["ao"].calls) == 2


def test_switch_off_only_hints(handed, monkeypatch, capsys):
    monkeypatch.setenv(handed_off.FORWARD_ENV, "off")
    assert _hook(monkeypatch, AO_MSG) == 0
    assert handed["ao"].calls == []
    assert "Перешли это сообщение ей" in capsys.readouterr().out


@pytest.mark.parametrize("sender", ["proj-24", "management-14"])
def test_no_ping_pong_with_new_or_manager(handed, monkeypatch, capsys, sender):
    msg = AO_MSG.replace("tide-stack-17", sender)
    assert _hook(monkeypatch, msg) == 0
    assert handed["ao"].calls == []
    assert "не пересылаю" in capsys.readouterr().out


def test_human_words_and_holding_sessions_are_untouched(handed, monkeypatch, capsys):
    assert _hook(monkeypatch, "Гриша: как там доска?") == 0
    assert _hook(monkeypatch, AO_MSG, sid="new-sid") == 0   # новая нить не отдавала
    assert _hook(monkeypatch, AO_MSG, sid="someone-else") == 0
    assert handed["ao"].calls == []
    assert "нить передана" not in capsys.readouterr().out

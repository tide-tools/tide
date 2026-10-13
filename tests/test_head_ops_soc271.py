"""SOC-271: быстрые команды головы — ``tide ok`` и ``tide task``.

``tide ok N "текст"`` — ответ на письмо N своей нити: письмо в нить отправителя,
сигнал его ведущей сессии, квитанция и ``replied`` в журнале. ``tide task`` —
create → state → «Назначено» → ao send → пульс одним вызовом; тело уходит в
linear-as-manager через stdin списком аргументов (обратные кавычки целы).
"""

from __future__ import annotations

import subprocess

import pytest

from tide import cli, fields, head_ops, mail, registry, roster
from tide.arc import stream
from tide.hooks import role_gate
from tide.launcher import autohandoff


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    stream.new_thread(proj, "manager-api", goal="пульт")
    stream.new_thread(proj, "signing", goal="подпись")
    me = stream.new_session(proj, "manager-api", "manager-api", claude_session="sid-me")
    them = stream.new_session(proj, "signing", "signing", claude_session="sid-them")
    registry.record(home, "sid-me", "proj-55", "manager-api")
    registry.record(home, "sid-them", "proj-51", "signing")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.AO_SESSION_ENV, "proj-55")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-me")
    monkeypatch.delenv(head_ops.LAM_ENV, raising=False)
    monkeypatch.chdir(proj)
    sent = []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert", lambda text, **kw: 0)
    monkeypatch.setattr(mail, "_ao_terminated", lambda: set())
    return {"home": home, "proj": proj, "me": me, "them": them, "sent": sent,
            "my": mail.thread_dir("manager-api"), "their": mail.thread_dir("signing")}


# --- tide ok ---------------------------------------------------------------------

def test_ok_replies_to_sender_thread_signals_and_marks(world, capsys):
    letter = mail.send("manager-api", "можно мержить?", sender="proj-51", do_ping=False)
    assert cli.main(["ok", letter["num"], "принято, мержи"]) == 0
    out = capsys.readouterr().out
    # ответ лёг письмом в нить отправителя, сигнал — его ведущей сессии, сразу
    reply = mail.letters(world["their"])[-1]
    assert reply["sender"] == "proj-55"
    assert reply["first"].startswith("принято, мержи — ответ на письмо 001")
    assert [h for h, _t in world["sent"]] == ["proj-51"]
    ping = world["sent"][0][1]
    assert "tide mail read proj/signing {0}".format(reply["num"]) in ping
    assert len(ping.encode("utf-8")) < 500
    # своё письмо — прочитано, в журнале replied со ссылкой на ответ
    assert mail.unread(world["my"]) == []
    rep = [e for e in mail.events(world["my"]) if e.get("ev") == head_ops.REPLIED_EV]
    assert rep and rep[0]["num"] == "001" and rep[0]["reply"] == reply["num"]
    assert "письмо {0} в proj/signing".format(reply["num"]) in out


def test_ok_without_text_says_ok(world):
    mail.send("manager-api", "готово", sender="proj-51", do_ping=False)
    assert cli.main(["ok", "1"]) == 0
    assert mail.letters(world["their"])[-1]["first"].startswith("ок — ответ на письмо 001")


def test_ok_dry_run_writes_nothing(world, capsys):
    mail.send("manager-api", "вопрос", sender="proj-51", do_ping=False)
    assert cli.main(["ok", "1", "да", "--dry-run"]) == 0
    assert "proj/signing" in capsys.readouterr().out
    assert mail.letters(world["their"]) == []
    assert world["sent"] == []
    assert len(mail.unread(world["my"])) == 1


def test_ok_unknown_sender_fails_without_ack(world, capsys):
    mail.send("manager-api", "от человека", sender="человек", do_ping=False)
    assert cli.main(["ok", "1"]) == 1
    assert "--to" in capsys.readouterr().err
    assert len(mail.unread(world["my"])) == 1
    # --to чинит
    assert cli.main(["ok", "1", "--to", "proj/signing"]) == 0
    assert mail.unread(world["my"]) == []


def test_sender_in_signature_parens_resolves(world):
    assert head_ops.sender_thread("signing · 01 (proj-51)") == world["their"]
    assert head_ops.sender_thread("proj-51-4b") == world["their"]


# --- tide task -------------------------------------------------------------------

class _Lam:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        self.calls.append((argv, input))
        sub = argv[1]
        out = {"create": "SOC-999 https://linear.app/x/issue/SOC-999/proverka\n",
               "state": "SOC-999 -> Todo (unstarted)\n",
               "comment": "https://linear.app/x/issue/SOC-999#comment-1\n"}[sub]
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def lam(monkeypatch):
    fake = _Lam()
    monkeypatch.setattr(head_ops.subprocess, "run", fake)
    monkeypatch.setenv(head_ops.LAM_ENV, "/x/linear-as-manager")
    return fake


def test_task_full_chain(world, lam, capsys):
    body = "**Зачем:** `tide ok` с `обратными` кавычками $(rm -rf /)\n"
    assert cli.main(["task", "Проверка команды", "--to", "proj/signing",
                     "--parent", "SOC-1", "--body", body, "--project", "Пульт"]) == 0
    out = capsys.readouterr().out
    (c_argv, c_in), (s_argv, _), (m_argv, m_in) = lam.calls
    assert c_argv == ["/x/linear-as-manager", "create", "--title", "Проверка команды",
                      "--body", "-", "--parent", "SOC-1", "--project", "Пульт"]
    assert c_in == body  # тело — stdin, нетронутое
    assert s_argv == ["/x/linear-as-manager", "state", "SOC-999", "Todo"]
    assert m_argv == ["/x/linear-as-manager", "comment", "SOC-999", "-"]
    assert m_in == "Назначено: proj — signing (proj-51)"
    assert world["sent"] == [("proj-51", "задача SOC-999: Проверка команды — "
                                         "https://linear.app/x/issue/SOC-999/proverka")]
    ctx = (world["me"] / "arc.md").read_text(encoding="utf-8")
    assert "tide task: SOC-999 «Проверка команды» → proj/signing (proj-51)" in ctx
    assert out.startswith("SOC-999 https://")
    assert "FAIL" not in out


def test_task_default_body_is_template_and_dry_run(world, lam, capsys):
    assert cli.main(["task", "x", "--to", "proj/signing", "--parent", "SOC-1",
                     "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert lam.calls == [] and world["sent"] == []
    for head in ("**Зачем:**", "**Результат:**", "**Как проверить:**", "**Ссылки:** SOC-1"):
        assert head in out
    assert cli.main(["task", "x", "--to", "proj/signing", "--state", "-"]) == 0
    assert [c[0][1] for c in lam.calls] == ["create", "comment"]
    assert "**Как проверить:**" in lam.calls[0][1]


def test_task_create_failure_stops(world, monkeypatch, capsys):
    monkeypatch.setenv(head_ops.LAM_ENV, "/x/lam")
    monkeypatch.setattr(head_ops.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, "", "boom"))
    assert cli.main(["task", "x", "--to", "proj/signing"]) == 1
    assert "boom" in capsys.readouterr().err
    assert world["sent"] == []


def test_lam_path_is_role_gate_constant(monkeypatch):
    monkeypatch.delenv(head_ops.LAM_ENV, raising=False)
    assert head_ops.lam_path() == role_gate._LINEAR_AS_MANAGER


# --- role-gate: голове разрешено ---------------------------------------------------

@pytest.mark.parametrize("command", [
    'tide ok 12 "принято"',
    'tide task "x" --to management/signing --parent SOC-1',
])
def test_role_gate_allows_head(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow

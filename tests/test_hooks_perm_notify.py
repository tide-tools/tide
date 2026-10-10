"""SOC-108 — ``tide hook perm-notify``: запрос разрешения сообщается в TG, без решения."""

from __future__ import annotations

import io
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone

import pytest

from tide import harness
from tide.hooks import perm_notify

NOW = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / ".tide").mkdir(parents=True)
    return root


@pytest.fixture
def alert(tmp_path, monkeypatch):
    """Подставной tg-alert: пишет argv и env в файл, ничего не шлёт."""
    log = tmp_path / "alerts.jsonl"
    script = tmp_path / "fake-alert"
    # sh, не python: старт за миллисекунды, в бюджет хука укладывается и под нагрузкой
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\t%s\\t%s\\t%s\\n' \"$TIDE_ALERT_ROUTE\" \"$TIDE_ALERT_SOURCE\" "
        "\"$1\" \"$2\" >> '{0}'\n".format(str(log)), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TIDE_ALERT_CMD", str(script))
    # Бюджет хука (0.8 с на tg-alert) проверяет test_slow_alert_cmd_is_cut_fast; здесь
    # проверяется текст сообщения, и под нагрузкой (mac2, load ~350) форк sh не успевал
    # за 0.8 с → сообщение срезалось, _alerts() пуст. Даём подставному пульту время.
    monkeypatch.setattr(perm_notify, "ALERT_TIMEOUT_SEC", 30.0)
    return log


@pytest.fixture
def ao(monkeypatch):
    monkeypatch.setenv("AO_SESSION_ID", "tide-stack-99")
    monkeypatch.delenv("TIDE_PERM_NOTIFY", raising=False)


def _payload(proj, command="rm -rf build", tool="Bash"):
    return {
        "hook_event_name": "PermissionRequest",
        "session_id": "claude-sid-1",
        "transcript_path": "/nonexistent.jsonl",
        "cwd": str(proj),
        "permission_mode": "default",
        "tool_name": tool,
        "tool_input": {"command": command, "description": "x"},
    }


def _rows(proj):
    path = proj / ".tide" / "state" / "blocked-perms.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]


def _alerts(log):
    if not log.is_file():
        return []
    out = []
    for ln in log.read_text(encoding="utf-8").splitlines():
        route, source, a1, a2 = ln.split("\t")
        out.append({"route": route, "source": source, "argv": [a1, a2]})
    return out


# --- запись строки + сообщение --------------------------------------------------

def test_writes_row_and_queues_message(ao, proj, alert):
    row = perm_notify.notify(_payload(proj), now=NOW)
    assert row is not None
    rows = _rows(proj)
    assert len(rows) == 1
    r = rows[0]
    assert r["ao_session"] == "tide-stack-99"
    assert r["project"] == "proj"
    assert r["tool"] == "Bash"
    assert r["input"] == "rm -rf build"
    assert r["ts"] == "2026-10-09T08:00:00Z"
    assert r["claude_session"] == "claude-sid-1"
    for k in ("id", "thread", "role", "cwd", "key"):
        assert k in r
    sent = _alerts(alert)
    assert len(sent) == 1
    argv = sent[0]["argv"]
    assert argv[0] == "--html"
    text = argv[1]
    assert text.startswith("❓ <b>proj (tide-stack-99)</b> ждёт разрешения в окне: ")
    assert "<code>Bash</code> <code>rm -rf build</code>" in text
    assert text.endswith("Решить можно только в окне сессии; ответ здесь пока ничего не применяет.")
    assert sent[0]["route"] == "human"
    assert "--poll" not in argv


def test_message_is_valid_one_line_html(ao, proj, alert):
    perm_notify.notify(_payload(proj, command="echo '<a>' && x > y & z\nline2"), now=NOW)
    text = _alerts(alert)[0]["argv"][1]
    assert "\n" not in text
    assert "&lt;a&gt;" in text and "&amp;" in text


def test_input_is_truncated(ao, proj, alert):
    perm_notify.notify(_payload(proj, command="echo " + "a " * 400), now=NOW)
    inp = _rows(proj)[0]["input"]
    assert len(inp) <= perm_notify.INPUT_MAX
    assert inp.endswith("…")


def test_path_tools_use_file_path(ao, proj, alert):
    payload = _payload(proj, tool="Write")
    payload["tool_input"] = {"file_path": "/x/y.py", "content": "secret body"}
    perm_notify.notify(payload, now=NOW)
    assert _rows(proj)[0]["input"] == "/x/y.py"


# --- маскирование -------------------------------------------------------------------

@pytest.mark.parametrize("raw,leak", [
    ("export ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnop"),
    ("curl -H 'Authorization: Bearer abcdefghijklmnop123' https://api", "abcdefghijklmnop123"),
    ("gh auth login --with-token ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdef"),
    ("psql postgres://user:hunter2pass@db/x", "hunter2pass"),
    ("tool --password s3cr3tvalue", "s3cr3tvalue"),
    ("TOKEN=lin_api_abcdefghijklmnopqrstuvwxyz012345 run", "lin_api_abc"),
    ("curl https://api.telegram.org/bot123456789:AAFabcdefghijklmnopqrstuvwxyz0123456/x",
     "AAFabcdefghijklmnop"),
    ("echo 0123456789abcdef0123456789abcdef0123", "0123456789abcdef0123456789abcdef"),
])
def test_secrets_are_masked(ao, proj, alert, raw, leak):
    perm_notify.notify(_payload(proj, command=raw), now=NOW)
    inp = _rows(proj)[0]["input"]
    assert leak not in inp
    assert perm_notify.MASK in inp
    assert leak not in _alerts(alert)[0]["argv"][1]


def test_plain_command_is_not_masked():
    assert perm_notify.mask_secrets("git log --author Grisha && pwd") == \
        "git log --author Grisha && pwd"


# --- дедуп --------------------------------------------------------------------------

def test_dedup_same_request_within_10_min(ao, proj, alert):
    assert perm_notify.notify(_payload(proj), now=NOW) is not None
    assert perm_notify.notify(_payload(proj), now=NOW + timedelta(minutes=9)) is None
    assert len(_rows(proj)) == 1
    assert len(_alerts(alert)) == 1
    # другой вход — отдельный запрос
    assert perm_notify.notify(_payload(proj, command="ls"), now=NOW + timedelta(minutes=1))
    # тот же через 10+ мин — снова
    assert perm_notify.notify(_payload(proj), now=NOW + timedelta(minutes=11)) is not None
    assert len(_rows(proj)) == 3
    assert len(_alerts(alert)) == 3


def test_dedup_is_per_session(ao, proj, alert, monkeypatch):
    perm_notify.notify(_payload(proj), now=NOW)
    monkeypatch.setenv("AO_SESSION_ID", "tide-stack-100")
    assert perm_notify.notify(_payload(proj), now=NOW) is not None


# --- молчание -----------------------------------------------------------------------

def test_non_ao_session_does_nothing(proj, alert, monkeypatch):
    monkeypatch.delenv("AO_SESSION_ID", raising=False)
    assert perm_notify.notify(_payload(proj), now=NOW) is None
    assert _rows(proj) == []
    assert _alerts(alert) == []


def test_off_switch(ao, proj, alert, monkeypatch):
    monkeypatch.setenv("TIDE_PERM_NOTIFY", "off")
    assert perm_notify.notify(_payload(proj), now=NOW) is None
    assert _rows(proj) == [] and _alerts(alert) == []


# --- CLI: пустой stdout, всегда 0 ------------------------------------------------------

def _run_cli(monkeypatch, capsys, stdin_text):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    rc = perm_notify.cmd_perm_notify(None)
    return rc, capsys.readouterr().out


def test_cli_stdout_is_empty_no_decision(ao, proj, alert, monkeypatch, capsys):
    rc, out = _run_cli(monkeypatch, capsys, json.dumps(_payload(proj)))
    assert rc == 0
    assert out == ""
    assert len(_rows(proj)) == 1


def test_cli_bad_json_is_quiet(ao, monkeypatch, capsys):
    rc, out = _run_cli(monkeypatch, capsys, "{not json")
    assert rc == 0 and out == ""


def test_outbox_error_exits_zero(ao, proj, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TIDE_ALERT_CMD", str(tmp_path / "no-such-tg-alert"))
    rc, out = _run_cli(monkeypatch, capsys, json.dumps(_payload(proj)))
    assert rc == 0 and out == ""
    assert _rows(proj)[0]["tool"] == "Bash"  # журнал лёг, хоть пульт и не ответил


def test_failing_alert_cmd_exits_zero(ao, proj, monkeypatch, capsys):
    monkeypatch.setenv("TIDE_ALERT_CMD", "false")
    rc, out = _run_cli(monkeypatch, capsys, json.dumps(_payload(proj)))
    assert rc == 0 and out == ""


def test_journal_error_exits_zero(ao, proj, alert, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(perm_notify, "recently_sent", boom)
    rc, out = _run_cli(monkeypatch, capsys, json.dumps(_payload(proj)))
    assert rc == 0 and out == ""


def test_slow_alert_cmd_is_cut_fast(ao, proj, monkeypatch, capsys, tmp_path):
    slow = tmp_path / "slow-alert"
    slow.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TIDE_ALERT_CMD", str(slow))
    t0 = time.monotonic()
    rc, out = _run_cli(monkeypatch, capsys, json.dumps(_payload(proj)))
    assert time.monotonic() - t0 < 2.0
    assert rc == 0 and out == ""


# --- раскатка: не входит в merge_hooks --------------------------------------------------

def test_not_wired_by_install_hooks():
    data: dict = {}
    harness.merge_hooks(data)
    assert harness.PERMISSION_REQUEST_EVENT not in data["hooks"]
    assert all(cmd != harness.PERM_NOTIFY_CMD for _, cmd in harness.TIDE_HOOKS)


def test_merge_perm_notify_is_idempotent():
    hooks: dict = {}
    assert harness.merge_perm_notify(hooks) is True
    assert harness.merge_perm_notify(hooks) is False
    assert hooks == {"PermissionRequest": [
        {"hooks": [{"type": "command", "command": "tide hook perm-notify"}]}]}

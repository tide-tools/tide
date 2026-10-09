"""SOC-108 — ``tide hook ask-gate``: в AO-сессии без человека вопрос идёт менеджеру."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from tide.hooks import ask_gate

NOW = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    t = NOW - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "{0:03d}Z".format(t.microsecond // 1000)


def _user(text, minutes_ago, kind="human", uid=None, content=None):
    rec = {"type": "user", "timestamp": _ts(minutes_ago), "origin": {"kind": kind},
           "uuid": uid or "u-{0}-{1}".format(kind, minutes_ago),
           "message": {"role": "user", "content": content if content is not None else text}}
    return rec


SPAWN = _user("Ты запущен tide через Agent Orchestrator.", 60, uid="spawn")


def _transcript(tmp_path, *records):
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "summary"}) + "\n")
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / ".tide").mkdir(parents=True)
    return root


@pytest.fixture
def ao(monkeypatch, tmp_path):
    monkeypatch.setenv("AO_SESSION_ID", "tide-stack-99")
    monkeypatch.delenv("TIDE_ASK_GATE", raising=False)
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(tmp_path / "no-manager.json"))


def _payload(proj, transcript, tool="AskUserQuestion"):
    return {
        "tool_name": tool,
        "session_id": "claude-sid-1",
        "transcript_path": str(transcript),
        "cwd": str(proj),
        "tool_input": {"questions": [{
            "question": "Какой вариант?", "header": "Выбор", "multiSelect": False,
            "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}],
        }]},
    }


def _deny(payload):
    return ask_gate.deny_reason(payload, now=NOW)


# --- решение -------------------------------------------------------------------

def test_deny_in_ao_without_live_human(ao, proj, tmp_path):
    reason = _deny(_payload(proj, _transcript(tmp_path, SPAWN)))
    assert reason is not None
    assert reason.startswith(
        "Это не поломка: вопрос по правилу идёт менеджеру текстом. "
        "Вопрос не диалогом: это сессия под AO")
    assert "за последние 10 мин" in reason
    assert ask_gate.MANAGER_FALLBACK in reason


def test_allow_with_live_human_two_minutes_ago(ao, proj, tmp_path):
    t = _transcript(tmp_path, SPAWN, _user("да, давай", 2))
    assert _deny(_payload(proj, t)) is None
    assert not (proj / ".tide" / "state" / "blocked-asks.jsonl").exists()


def test_allow_with_text_block_list(ao, proj, tmp_path):
    rec = _user(None, 1, content=[{"type": "text", "text": "ок"}])
    assert _deny(_payload(proj, _transcript(tmp_path, SPAWN, rec))) is None


def test_deny_with_live_human_thirty_minutes_ago(ao, proj, tmp_path):
    t = _transcript(tmp_path, SPAWN, _user("да, давай", 30))
    assert _deny(_payload(proj, t)) is not None


def test_allow_without_ao_session(monkeypatch, proj, tmp_path):
    monkeypatch.delenv("AO_SESSION_ID", raising=False)
    assert _deny(_payload(proj, _transcript(tmp_path, SPAWN))) is None


def test_allow_when_switched_off(ao, monkeypatch, proj, tmp_path):
    monkeypatch.setenv("TIDE_ASK_GATE", "off")
    assert _deny(_payload(proj, _transcript(tmp_path, SPAWN))) is None


def test_allow_other_tool(ao, proj, tmp_path):
    assert _deny(_payload(proj, _transcript(tmp_path, SPAWN), tool="Bash")) is None


@pytest.mark.parametrize("rec", [
    _user('<ao-session-delivery id="x">\n[from management-9] привет', 1),
    _user("  [tg] Гриша: привет", 1),
    _user("<task-notification>done</task-notification>", 1),
    _user("<system-reminder>x</system-reminder>", 1),
    _user("Another Claude session sent a message: hi", 1),
    _user("сообщение соседа", 1, kind="peer"),
    _user(None, 1, content=[{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]),
])
def test_not_human_replies_do_not_count(ao, proj, tmp_path, rec):
    assert _deny(_payload(proj, _transcript(tmp_path, SPAWN, rec))) is not None


def test_first_human_record_is_spawn_prompt(ao, proj, tmp_path):
    fresh_spawn = _user("Ты запущен tide через Agent Orchestrator.", 1, uid="spawn")
    assert _deny(_payload(proj, _transcript(tmp_path, fresh_spawn))) is not None


def test_reads_only_tail(ao, proj, tmp_path, monkeypatch):
    # Хвост короче файла: свежая реплика в хвосте видна, утонувшая до хвоста — нет.
    pad = [{"type": "assistant", "message": {"content": "x" * 200}} for _ in range(50)]
    monkeypatch.setattr(ask_gate, "TAIL_BYTES", 2_000)
    in_tail = _transcript(tmp_path, SPAWN, *pad, _user("да", 1))
    assert _deny(_payload(proj, in_tail)) is None
    sunk = _transcript(tmp_path, SPAWN, _user("да", 1), *pad)
    assert _deny(_payload(proj, sunk)) is not None


# --- запись вопроса --------------------------------------------------------------

def test_blocked_ask_recorded(ao, proj, tmp_path):
    reason = _deny(_payload(proj, _transcript(tmp_path, SPAWN)))
    path = proj / ".tide" / "state" / "blocked-asks.jsonl"
    assert path.is_file()
    assert "Вопрос записан: {0}.".format(path) in reason
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"id", "ts", "project", "ao_session", "claude_session",
                        "cwd", "questions", "answered"}
    assert len(row["id"]) == 36
    assert row["ts"] == "2026-10-09T08:00:00Z"
    assert row["project"] == "proj"
    assert row["ao_session"] == "tide-stack-99"
    assert row["claude_session"] == "claude-sid-1"
    assert row["cwd"] == str(proj)
    assert row["questions"] == [{"question": "Какой вариант?", "header": "Выбор",
                                 "options": ["A", "B"]}]
    assert row["answered"] is None


def test_not_recorded_outside_tide_project(ao, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    reason = _deny(_payload(bare, _transcript(tmp_path, SPAWN)))
    assert reason.endswith("Вопрос записан: не записан.")


# --- адрес менеджера -------------------------------------------------------------

def test_manager_address_from_file(ao, monkeypatch, proj, tmp_path):
    mf = tmp_path / "manager.json"
    mf.write_text(json.dumps({"ao_session": "ao-control-7", "uds_name": "management-9-b5",
                              "updated_at": "2026-10-09T08:00:00Z"}), encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mf))
    reason = _deny(_payload(proj, _transcript(tmp_path, SPAWN)))
    assert "SendMessage: management-9-b5 (ao_session ao-control-7)." in reason


@pytest.fixture
def no_chain(monkeypatch):
    from tide import whois
    monkeypatch.setattr(whois, "_home", lambda: None)
    monkeypatch.setattr(whois.paths, "find_tide_root", lambda *a, **k: None)


def test_manager_address_fallback(tmp_path, no_chain):
    assert ask_gate.manager_address(tmp_path / "missing.json") == ask_gate.MANAGER_FALLBACK
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert ask_gate.manager_address(broken) == ask_gate.MANAGER_FALLBACK
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert ask_gate.manager_address(empty) == ask_gate.MANAGER_FALLBACK


def test_manager_fallback_has_no_constant_addresses(tmp_path, no_chain):
    # SOC-101 C14: константы устаревают на первом хендоффе — только резолвер
    addr = ask_gate.manager_address(tmp_path / "missing.json")
    assert "management-" not in addr and "ao-control" not in addr
    assert "tide whois manager" in addr


def test_manager_address_from_new_format(tmp_path):
    mf = tmp_path / "manager.json"
    mf.write_text(json.dumps({"ao_session": "management-14", "thread": "main",
                              "title": "management — main · 04"}), encoding="utf-8")
    assert ask_gate.manager_address(mf) == \
        "management — main · 04 (ao_session management-14)"


# --- CLI-обёртка ---------------------------------------------------------------

def _run(monkeypatch, capsys, stdin: str):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    rc = ask_gate.cmd_ask_gate(None)
    return rc, capsys.readouterr().out


def test_cli_deny_prints_pretooluse_json(ao, monkeypatch, capsys, proj, tmp_path):
    rc, out = _run(monkeypatch, capsys,
                   json.dumps(_payload(proj, _transcript(tmp_path, SPAWN))))
    assert rc == 0
    spec = json.loads(out)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith(
        "Это не поломка: вопрос по правилу идёт менеджеру текстом.")


@pytest.mark.parametrize("stdin", ["", "{ not json", "[1, 2]", '"str"'])
def test_cli_broken_input_passes(ao, monkeypatch, capsys, stdin):
    rc, out = _run(monkeypatch, capsys, stdin)
    assert rc == 0 and out == ""


def test_cli_other_tool_passes_silently(ao, monkeypatch, capsys, proj, tmp_path):
    rc, out = _run(monkeypatch, capsys,
                   json.dumps(_payload(proj, _transcript(tmp_path, SPAWN), tool="Edit")))
    assert rc == 0 and out == ""


def test_cli_exception_fails_open(ao, monkeypatch, capsys, proj, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ask_gate, "deny_reason", boom)
    rc, out = _run(monkeypatch, capsys,
                   json.dumps(_payload(proj, _transcript(tmp_path, SPAWN))))
    assert rc == 0 and out == ""


def test_merge_hooks_wires_ask_gate():
    from tide import harness

    data: dict = {}
    harness.merge_hooks(data)
    cmds = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert cmds.count(harness.ASK_GATE_CMD) == 1
    assert harness.merge_hooks(data) == []

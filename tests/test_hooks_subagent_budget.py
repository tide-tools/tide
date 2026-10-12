"""SOC-237 — ``tide hook subagent-budget``: мягкое предупреждение о веере сабагентов."""

from __future__ import annotations

import io
import json

from tide.hooks import subagent_budget as sb


def _payload(tmp_path, tool="Agent", sid="s1"):
    return {"tool_name": tool, "session_id": sid, "cwd": str(tmp_path)}


def _root(tmp_path):
    root = tmp_path / "proj"
    (root / ".tide").mkdir(parents=True)
    return root


def test_no_warning_under_ceilings(tmp_path):
    root = _root(tmp_path)
    for i in range(sb.MAX_BURST):
        assert sb.register_spawn(_payload(tmp_path), now=1000.0 + i, root=root) is None


def test_burst_warns_but_does_not_block(tmp_path):
    root = _root(tmp_path)
    out = None
    for i in range(sb.MAX_BURST + 1):
        out = sb.register_spawn(_payload(tmp_path), now=1000.0 + i, root=root)
    assert out and "потолок" in out.lower()
    shape = sb.warn_output(out)
    assert "permissionDecision" not in shape["hookSpecificOutput"]
    assert shape["hookSpecificOutput"]["additionalContext"] == out


def test_total_ceiling_over_time(tmp_path):
    root = _root(tmp_path)
    res = [sb.register_spawn(_payload(tmp_path), now=1000.0 + i * 1000, root=root)
           for i in range(sb.MAX_TOTAL + 1)]
    assert all(r is None for r in res[:sb.MAX_TOTAL])
    assert res[-1] and "{0}-й сабагент".format(sb.MAX_TOTAL + 1) in res[-1]


def test_other_tool_sessions_and_off_switch(tmp_path, monkeypatch):
    root = _root(tmp_path)
    assert sb.register_spawn(_payload(tmp_path, tool="Bash"), now=1.0, root=root) is None
    for i in range(sb.MAX_BURST + 2):  # другая сессия считается отдельно
        assert sb.register_spawn(_payload(tmp_path, sid="s{0}".format(i)), now=5.0, root=root) is None
    monkeypatch.setenv(sb.OFF_ENV, "off")
    for i in range(sb.MAX_TOTAL + 2):
        assert sb.register_spawn(_payload(tmp_path), now=9.0 + i, root=root) is None


def test_cmd_fail_open_and_prints_json(tmp_path, monkeypatch, capsys):
    _root(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert sb.cmd_subagent_budget(None) == 0
    assert capsys.readouterr().out == ""
    for i in range(sb.MAX_BURST + 1):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"tool_name": "Agent", "session_id": "x", "cwd": str(tmp_path / "proj")})))
        assert sb.cmd_subagent_budget(None) == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

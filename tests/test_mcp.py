"""Tests for tide.mcp — per-project scoped MCP server management.

Exercises the two files edited in lock-step: ``.tide/state/mcp.json`` (the scoped
config, with a remembered ``_disabled`` map) and ``.tide/state/context.json``
(``mcp_config`` set on first add, cleared when the active set empties).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import mcp, paths


def _read_mcp_json(root: Path) -> dict:
    return json.loads(paths.mcp_file(root).read_text(encoding="utf-8"))


def _ctx_mcp_config(root: Path):
    f = paths.context_file(root)
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8")).get("mcp_config")


# --- serverdef construction ------------------------------------------------

def test_build_serverdef_http_explicit():
    sd = mcp.build_serverdef("example.com/mcp", http=True)
    assert sd == {"type": "http", "url": "example.com/mcp"}


def test_build_serverdef_http_by_scheme():
    sd = mcp.build_serverdef("https://api.example.com/mcp")
    assert sd == {"type": "http", "url": "https://api.example.com/mcp"}


def test_build_serverdef_command_splits_argv():
    sd = mcp.build_serverdef("npx -y @scope/server --flag")
    assert sd == {"command": "npx", "args": ["-y", "@scope/server", "--flag"]}


def test_build_serverdef_empty_raises():
    with pytest.raises(mcp.McpError):
        mcp.build_serverdef("   ")


# --- add: http ------------------------------------------------------------

def test_add_http_updates_both_files(tmp_project):
    mcp.add_server(tmp_project, "weather", "https://w.example/mcp")

    data = _read_mcp_json(tmp_project)
    assert data["mcpServers"]["weather"] == {"type": "http", "url": "https://w.example/mcp"}

    # context.json now points at mcp.json
    assert _ctx_mcp_config(tmp_project) == str(paths.mcp_file(tmp_project))
    ctx = json.loads(paths.context_file(tmp_project).read_text(encoding="utf-8"))
    assert ctx["strict_mcp"] is True


# --- add: command ---------------------------------------------------------

def test_add_command_parses_argv(tmp_project):
    sd = mcp.add_server(tmp_project, "fs", "node server.js --port 3000")
    assert sd == {"command": "node", "args": ["server.js", "--port", "3000"]}
    data = _read_mcp_json(tmp_project)
    assert data["mcpServers"]["fs"]["command"] == "node"


# --- off / on round-trip through _disabled --------------------------------

def test_off_on_round_trip(tmp_project):
    mcp.add_server(tmp_project, "a", "https://a.example/mcp")
    mcp.add_server(tmp_project, "b", "https://b.example/mcp")

    mcp.disable_server(tmp_project, "a")
    data = _read_mcp_json(tmp_project)
    assert "a" not in data["mcpServers"]
    assert data["_disabled"]["a"] == {"type": "http", "url": "https://a.example/mcp"}
    # b still active → context still points at mcp.json
    assert _ctx_mcp_config(tmp_project) == str(paths.mcp_file(tmp_project))

    mcp.enable_server(tmp_project, "a")
    data = _read_mcp_json(tmp_project)
    assert "a" in data["mcpServers"]
    assert "a" not in data.get("_disabled", {})


def test_context_cleared_when_last_disabled(tmp_project):
    mcp.add_server(tmp_project, "only", "https://o.example/mcp")
    assert _ctx_mcp_config(tmp_project) is not None

    mcp.disable_server(tmp_project, "only")
    # active set is now empty → context mcp_config cleared (back to lean)
    assert _ctx_mcp_config(tmp_project) is None
    # but the config is remembered under _disabled
    assert "only" in _read_mcp_json(tmp_project)["_disabled"]


# --- rm -------------------------------------------------------------------

def test_rm_removes_and_clears_context_when_last(tmp_project):
    mcp.add_server(tmp_project, "only", "https://o.example/mcp")
    mcp.remove_server(tmp_project, "only")

    data = _read_mcp_json(tmp_project)
    assert data["mcpServers"] == {}
    assert "only" not in data.get("_disabled", {})
    assert _ctx_mcp_config(tmp_project) is None


def test_rm_absent_raises(tmp_project):
    with pytest.raises(mcp.McpError):
        mcp.remove_server(tmp_project, "ghost")


def test_rm_disabled_server(tmp_project):
    mcp.add_server(tmp_project, "x", "https://x.example/mcp")
    mcp.disable_server(tmp_project, "x")
    mcp.remove_server(tmp_project, "x")
    data = _read_mcp_json(tmp_project)
    assert "x" not in data["mcpServers"] and "x" not in data.get("_disabled", {})


# --- toggle errors / idempotency ------------------------------------------

def test_disable_absent_raises(tmp_project):
    with pytest.raises(mcp.McpError):
        mcp.disable_server(tmp_project, "nope")


def test_enable_absent_raises(tmp_project):
    with pytest.raises(mcp.McpError):
        mcp.enable_server(tmp_project, "nope")


def test_disable_already_off_is_idempotent(tmp_project):
    mcp.add_server(tmp_project, "x", "https://x.example/mcp")
    mcp.disable_server(tmp_project, "x")
    mcp.disable_server(tmp_project, "x")  # no raise
    assert "x" in _read_mcp_json(tmp_project)["_disabled"]


# --- list -----------------------------------------------------------------

def test_list_empty(tmp_project):
    assert mcp.render_list(tmp_project) == "(no MCP servers)"


def test_list_shows_on_and_off(tmp_project):
    mcp.add_server(tmp_project, "live", "https://live.example/mcp")
    mcp.add_server(tmp_project, "cmd", "node s.js")
    mcp.disable_server(tmp_project, "cmd")

    out = mcp.render_list(tmp_project)
    assert "ON   live  https://live.example/mcp" in out
    assert "OFF  cmd  node s.js" in out


# --- CLI round-trip --------------------------------------------------------

def test_cli_round_trip(tmp_project, monkeypatch, capsys):
    from tide import cli

    monkeypatch.chdir(tmp_project)

    assert cli.main(["mcp", "add", "weather", "https://w.example/mcp"]) == 0
    capsys.readouterr()

    assert cli.main(["mcp", "list"]) == 0
    assert "ON   weather" in capsys.readouterr().out

    assert cli.main(["mcp", "off", "weather"]) == 0
    capsys.readouterr()
    assert _ctx_mcp_config(tmp_project) is None

    assert cli.main(["mcp", "on", "weather"]) == 0
    capsys.readouterr()
    assert _ctx_mcp_config(tmp_project) == str(paths.mcp_file(tmp_project))

    assert cli.main(["mcp", "rm", "weather"]) == 0
    capsys.readouterr()
    assert mcp.render_list(tmp_project) == "(no MCP servers)"


# --- env support (cand 26) -------------------------------------------------

def test_build_serverdef_command_with_env():
    sd = mcp.build_serverdef("godot-mcp", env={"GODOT_PATH": "/opt/godot"})
    assert sd == {"command": "godot-mcp", "args": [], "env": {"GODOT_PATH": "/opt/godot"}}


def test_build_serverdef_no_env_key_when_absent():
    sd = mcp.build_serverdef("some-cmd --flag")
    assert "env" not in sd


def test_build_serverdef_env_on_http_is_an_error():
    with pytest.raises(mcp.McpError, match="command server"):
        mcp.build_serverdef("https://x.example/mcp", env={"K": "V"})


def test_parse_env_pairs():
    assert mcp._parse_env(["A=1", "B=two=three"]) == {"A": "1", "B": "two=three"}
    assert mcp._parse_env(None) == {}


def test_parse_env_rejects_malformed():
    with pytest.raises(mcp.McpError, match="KEY=VAL"):
        mcp._parse_env(["NOEQUALS"])
    with pytest.raises(mcp.McpError, match="empty key"):
        mcp._parse_env(["=v"])


def test_add_server_persists_env(tmp_project):
    mcp.add_server(tmp_project, "godot", "godot-mcp", env={"GODOT_PATH": "/opt/godot"})
    data = _read_mcp_json(tmp_project)
    assert data["mcpServers"]["godot"]["env"] == {"GODOT_PATH": "/opt/godot"}


def test_summarize_shows_env_keys():
    sd = mcp.build_serverdef("godot-mcp", env={"GODOT_PATH": "/x", "API_KEY": "y"})
    line = mcp.summarize(sd)
    assert "env:" in line and "API_KEY" in line and "GODOT_PATH" in line


def test_cli_add_with_env_flag(tmp_project, monkeypatch):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["mcp", "add", "godot", "godot-mcp", "-e", "GODOT_PATH=/opt/godot"])
    assert rc == 0
    data = _read_mcp_json(tmp_project)
    assert data["mcpServers"]["godot"]["env"] == {"GODOT_PATH": "/opt/godot"}

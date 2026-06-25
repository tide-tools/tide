"""U10 — `tide install-hooks`: SessionStart + PreToolUse, MERGE-not-clobber."""

from __future__ import annotations

import json

import pytest

from tide import cli
from tide.hooks import install


# --- pure merge ------------------------------------------------------------

def test_merge_hooks_into_empty_writes_both_entries():
    data: dict = {}
    notes = install.merge_hooks(data)

    hooks = data["hooks"]
    assert install.SESSION_START_EVENT in hooks
    assert install.PRE_TOOL_USE_EVENT in hooks
    # SessionStart points at the right command.
    ss_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert ss_cmd == install.SESSION_START_CMD
    # PreToolUse carries the Edit|Write|MultiEdit matcher + edit-gate command.
    pre = hooks["PreToolUse"]
    edit_gate_group = next(
        g for g in pre if g.get("matcher") == install.EDIT_MATCHER
    )
    assert edit_gate_group["hooks"][0]["command"] == install.EDIT_GATE_CMD
    # PreToolUse also carries the role-gate group.
    role_gate_group = next(
        g for g in pre if g.get("matcher") == install.ROLE_GATE_MATCHER
    )
    assert role_gate_group["hooks"][0]["command"] == install.ROLE_GATE_CMD
    # Three notes: SessionStart + edit-gate + role-gate.
    assert len(notes) == 3


def test_merge_hooks_is_idempotent():
    data: dict = {}
    install.merge_hooks(data)
    notes = install.merge_hooks(data)  # second pass
    assert notes == []
    # No duplicate groups.
    assert len(data["hooks"]["SessionStart"]) == 1
    # Two PreToolUse groups: edit-gate + role-gate (not duplicated).
    assert len(data["hooks"]["PreToolUse"]) == 2


def test_merge_preserves_existing_hooks_and_keys():
    # A realistic settings.json carrying the human's own rtk PreToolUse + perms.
    data = {
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "rtk wrap"}],
                }
            ]
        },
    }
    install.merge_hooks(data)

    # Pre-existing permissions untouched.
    assert data["permissions"] == {"allow": ["Bash(git status)"]}
    # The rtk Bash group still there; edit-gate + role-gate appended (not replacing).
    pre = data["hooks"]["PreToolUse"]
    commands = [g["hooks"][0]["command"] for g in pre]
    assert "rtk wrap" in commands
    assert install.EDIT_GATE_CMD in commands
    assert install.ROLE_GATE_CMD in commands
    assert len(pre) == 3


def test_merge_rejects_non_object_hooks():
    with pytest.raises(install.InstallError):
        install.merge_hooks({"hooks": ["not-a-dict"]})


# --- I/O -------------------------------------------------------------------

def test_install_hooks_writes_valid_settings_file(tmp_project):
    path, notes = install.install_hooks(tmp_project)
    assert path.is_file()
    assert path == tmp_project / ".claude" / "settings.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert "SessionStart" in parsed["hooks"]
    assert "PreToolUse" in parsed["hooks"]
    # Three entries: SessionStart + edit-gate + role-gate.
    assert len(notes) == 3


def test_install_hooks_rerun_idempotent(tmp_project):
    install.install_hooks(tmp_project)
    _, notes = install.install_hooks(tmp_project)
    assert notes == []
    parsed = json.loads(
        (tmp_project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    # Two PreToolUse groups: edit-gate + role-gate.
    assert len(parsed["hooks"]["PreToolUse"]) == 2


def test_install_hooks_rejects_corrupt_settings(tmp_project):
    settings = tmp_project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{ not json", encoding="utf-8")
    with pytest.raises(install.InstallError):
        install.install_hooks(tmp_project)


# --- CLI -------------------------------------------------------------------

def test_cli_install_hooks(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["install-hooks"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hooks wired" in out
    assert (tmp_project / ".claude" / "settings.json").is_file()


def test_cli_install_hooks_outside_project_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .tide/
    rc = cli.main(["install-hooks"])
    assert rc == 1
    assert "no .tide/" in capsys.readouterr().err

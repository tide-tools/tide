"""tide.hooks.role_gate — orchestrator role-capability gate tests.

Covers all six acceptance criteria:
  1. Orchestrator: Write/Edit/NotebookEdit denied with re-teaching message.
  2. Orchestrator: mutating Bash denied; read-only/tide Bash allowed.
  3. Read/Grep/Glob/Agent/Task always allowed (decide returns True).
  4. Worker / unset role = pure no-op (full Write/Edit/Bash allowed).
  5. ``tide hook role-gate`` dispatch wired (CLI roundtrip test).
  6. ``tide install-hooks`` registers the role-gate entry (install test is in
     test_hooks_install.py; spot-checked here for the merge helper).
"""

from __future__ import annotations

import io

import pytest

from tide import cli
from tide.hooks import install, role_gate


# ---------------------------------------------------------------------------
# Worker / unset role — pure no-op
# ---------------------------------------------------------------------------

def test_worker_allows_write():
    allow, reason = role_gate.decide("Write", {"file_path": "/tmp/x.py"}, "worker")
    assert allow is True
    assert reason == ""


def test_worker_allows_edit():
    allow, _ = role_gate.decide("Edit", {"file_path": "/tmp/x.py"}, "worker")
    assert allow is True


def test_worker_allows_notebook_edit():
    allow, _ = role_gate.decide("NotebookEdit", {"notebook_path": "/tmp/x.ipynb"}, "worker")
    assert allow is True


def test_worker_allows_mutating_bash():
    allow, _ = role_gate.decide("Bash", {"command": "rm -rf ."}, "worker")
    assert allow is True


def test_worker_allows_pip_install():
    allow, _ = role_gate.decide("Bash", {"command": "pip install requests"}, "worker")
    assert allow is True


def test_unset_role_allows_write():
    # Empty string is not "orchestrator" → treated as worker.
    allow, _ = role_gate.decide("Write", {"file_path": "/tmp/x.py"}, "")
    assert allow is True


def test_arbitrary_role_allows_write():
    allow, _ = role_gate.decide("Write", {}, "reviewer")
    assert allow is True


# ---------------------------------------------------------------------------
# Orchestrator — Write / Edit / NotebookEdit unconditionally denied
# ---------------------------------------------------------------------------

def test_orchestrator_denies_write():
    allow, reason = role_gate.decide("Write", {"file_path": "/tmp/x.py"}, "orchestrator")
    assert allow is False
    assert "HEAD (orchestrator)" in reason
    assert "worker-work" in reason


def test_orchestrator_denies_edit():
    allow, reason = role_gate.decide("Edit", {"file_path": "/tmp/x.py"}, "orchestrator")
    assert allow is False
    assert "Dispatch it via the Agent tool" in reason


def test_orchestrator_denies_notebook_edit():
    allow, reason = role_gate.decide(
        "NotebookEdit", {"notebook_path": "/tmp/x.ipynb"}, "orchestrator"
    )
    assert allow is False
    assert "tide CLI" in reason


# ---------------------------------------------------------------------------
# Orchestrator — Bash allowlist: tide commands always OK
# ---------------------------------------------------------------------------

def test_orchestrator_allows_bare_tide():
    allow, _ = role_gate.decide("Bash", {"command": "tide"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_tide_status():
    allow, _ = role_gate.decide("Bash", {"command": "tide status"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_tide_arc_new():
    allow, _ = role_gate.decide("Bash", {"command": "tide arc new my-arc"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_tide_cannon_merge():
    allow, _ = role_gate.decide("Bash", {"command": "tide cannon merge alpha"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_tide_install_hooks():
    allow, _ = role_gate.decide("Bash", {"command": "tide install-hooks"}, "orchestrator")
    assert allow is True


# ---------------------------------------------------------------------------
# Orchestrator — Bash allowlist: read-only git
# ---------------------------------------------------------------------------

def test_orchestrator_allows_git_status():
    allow, _ = role_gate.decide("Bash", {"command": "git status"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_log():
    allow, _ = role_gate.decide("Bash", {"command": "git log --oneline -10"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_diff():
    allow, _ = role_gate.decide("Bash", {"command": "git diff HEAD~1"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_show():
    allow, _ = role_gate.decide("Bash", {"command": "git show HEAD"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_branch_list():
    allow, _ = role_gate.decide("Bash", {"command": "git branch"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_branch_all():
    allow, _ = role_gate.decide("Bash", {"command": "git branch -a"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_branch_remote():
    allow, _ = role_gate.decide("Bash", {"command": "git branch -r"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_rev_parse():
    allow, _ = role_gate.decide("Bash", {"command": "git rev-parse HEAD"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_worktree_list():
    allow, _ = role_gate.decide("Bash", {"command": "git worktree list"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_remote():
    allow, _ = role_gate.decide("Bash", {"command": "git remote"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_git_remote_v():
    allow, _ = role_gate.decide("Bash", {"command": "git remote -v"}, "orchestrator")
    assert allow is True


# ---------------------------------------------------------------------------
# Orchestrator — Bash allowlist: read-only builtins
# ---------------------------------------------------------------------------

def test_orchestrator_allows_ls():
    allow, _ = role_gate.decide("Bash", {"command": "ls -la"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_cat():
    allow, _ = role_gate.decide("Bash", {"command": "cat file.txt"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_pwd():
    allow, _ = role_gate.decide("Bash", {"command": "pwd"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_find():
    allow, _ = role_gate.decide("Bash", {"command": "find . -name '*.py'"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_grep():
    allow, _ = role_gate.decide("Bash", {"command": "grep -r pattern ."}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_echo():
    allow, _ = role_gate.decide("Bash", {"command": "echo hello"}, "orchestrator")
    assert allow is True


# ---------------------------------------------------------------------------
# Orchestrator — Bash: denied mutating operations
# ---------------------------------------------------------------------------

def test_orchestrator_denies_echo_redirect():
    allow, _ = role_gate.decide(
        "Bash", {"command": "echo hello > file.txt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_append_redirect():
    allow, _ = role_gate.decide(
        "Bash", {"command": "echo more >> file.txt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_pipe():
    allow, _ = role_gate.decide(
        "Bash", {"command": "cat file.txt | sed 's/foo/bar/' > out.txt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_rm():
    allow, reason = role_gate.decide("Bash", {"command": "rm -rf ."}, "orchestrator")
    assert allow is False
    assert "HEAD (orchestrator)" in reason


def test_orchestrator_denies_mv():
    allow, _ = role_gate.decide("Bash", {"command": "mv file.txt other.txt"}, "orchestrator")
    assert allow is False


def test_orchestrator_denies_cp():
    allow, _ = role_gate.decide("Bash", {"command": "cp src dst"}, "orchestrator")
    assert allow is False


def test_orchestrator_denies_pip_install():
    allow, _ = role_gate.decide("Bash", {"command": "pip install requests"}, "orchestrator")
    assert allow is False


def test_orchestrator_denies_sed_i():
    allow, _ = role_gate.decide(
        "Bash", {"command": "sed -i 's/foo/bar/' file.txt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_commit():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git commit -m 'fix'"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_push():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git push origin main"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_merge():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git merge feature-branch"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_branch_delete():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git branch -D stale-branch"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_branch_delete_long():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git branch --delete stale-branch"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_worktree_add():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git worktree add /tmp/wt my-branch"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_worktree_remove():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git worktree remove /tmp/wt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_unknown_command():
    allow, _ = role_gate.decide("Bash", {"command": "python3 setup.py install"}, "orchestrator")
    assert allow is False


# ---------------------------------------------------------------------------
# Orchestrator — tools always allowed (Read/Grep/Glob/Agent/Task)
# ---------------------------------------------------------------------------

def test_orchestrator_allows_read_tool():
    allow, _ = role_gate.decide("Read", {"file_path": "/tmp/x.py"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_grep_tool():
    allow, _ = role_gate.decide("Grep", {"pattern": "foo", "path": "."}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_glob_tool():
    allow, _ = role_gate.decide("Glob", {"pattern": "**/*.py"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_agent_tool():
    allow, _ = role_gate.decide("Agent", {"prompt": "do work"}, "orchestrator")
    assert allow is True


def test_orchestrator_allows_task_tool():
    allow, _ = role_gate.decide("Task", {}, "orchestrator")
    assert allow is True


# ---------------------------------------------------------------------------
# CLI handler (stdin payload roundtrip)
# ---------------------------------------------------------------------------

def _run_role_gate(monkeypatch, payload_json: str, *, role: str = "orchestrator") -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload_json))
    monkeypatch.setenv("TIDE_ROLE", role)
    return cli.main(["hook", "role-gate"])


def test_cli_denies_write_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x.py","content":"x"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT
    err = capsys.readouterr().err
    assert "HEAD (orchestrator)" in err


def test_cli_denies_edit_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x.py"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT


def test_cli_denies_notebook_edit_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"NotebookEdit","tool_input":{"notebook_path":"/tmp/x.ipynb"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT


def test_cli_denies_mutating_bash_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"rm -rf ."}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT
    assert "HEAD (orchestrator)" in capsys.readouterr().err


def test_cli_allows_tide_bash_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"tide status"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_git_status_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"git status"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_write_for_worker(monkeypatch, capsys):
    payload = '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x.py","content":"x"}}'
    rc = _run_role_gate(monkeypatch, payload, role="worker")
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_bash_mutation_for_worker(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"rm -rf ."}}'
    rc = _run_role_gate(monkeypatch, payload, role="worker")
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_on_garbled_payload(monkeypatch):
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    rc = cli.main(["hook", "role-gate"])
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_on_empty_payload(monkeypatch):
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = cli.main(["hook", "role-gate"])
    assert rc == role_gate.ALLOW_EXIT


# ---------------------------------------------------------------------------
# install-hooks: merge_role_gate is included
# ---------------------------------------------------------------------------

def test_merge_hooks_includes_role_gate():
    data: dict = {}
    install.merge_hooks(data)
    hooks = data["hooks"]
    pre_groups = hooks.get(install.PRE_TOOL_USE_EVENT, [])
    all_cmds = [
        h["command"]
        for group in pre_groups
        for h in group.get(install.HOOKS_KEY, [])
        if isinstance(h, dict)
    ]
    assert install.ROLE_GATE_CMD in all_cmds


def test_merge_role_gate_is_idempotent():
    data: dict = {}
    install.merge_hooks(data)
    notes = install.merge_hooks(data)  # second pass
    assert notes == []
    pre_groups = data["hooks"][install.PRE_TOOL_USE_EVENT]
    role_gate_groups = [
        g for g in pre_groups
        if g.get("matcher") == install.ROLE_GATE_MATCHER
    ]
    assert len(role_gate_groups) == 1  # not duplicated


def test_merge_role_gate_preserves_existing_hooks():
    data = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk wrap"}]}
            ]
        }
    }
    install.merge_hooks(data)
    pre_groups = data["hooks"]["PreToolUse"]
    all_cmds = [
        h["command"]
        for group in pre_groups
        for h in group.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert "rtk wrap" in all_cmds
    assert install.ROLE_GATE_CMD in all_cmds


def test_install_hooks_writes_role_gate(tmp_project):
    import json

    path, notes = install.install_hooks(tmp_project)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    pre_groups = parsed["hooks"].get("PreToolUse", [])
    all_cmds = [
        h["command"]
        for g in pre_groups
        for h in g.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert install.ROLE_GATE_CMD in all_cmds
    # Three notes on first install: SessionStart + edit-gate + role-gate.
    assert len(notes) == 3

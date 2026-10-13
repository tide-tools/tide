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
    assert "голове (orchestrator)" in reason
    assert "работа для помощника" in reason


def test_orchestrator_denies_edit():
    allow, reason = role_gate.decide("Edit", {"file_path": "/tmp/x.py"}, "orchestrator")
    assert allow is False
    assert "отдай её через Agent" in reason


def test_orchestrator_denies_notebook_edit():
    allow, reason = role_gate.decide(
        "NotebookEdit", {"notebook_path": "/tmp/x.ipynb"}, "orchestrator"
    )
    assert allow is False
    assert "Write/Edit/NotebookEdit" in reason


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


def test_orchestrator_allows_tide_canon_merge():
    allow, _ = role_gate.decide("Bash", {"command": "tide canon merge alpha"}, "orchestrator")
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
# Orchestrator — global git flags that take a separate value token
# ---------------------------------------------------------------------------

def test_orchestrator_allows_git_dash_c_path_status():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -C /tmp status"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_allows_git_dash_c_path_status_with_flag():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -C /tmp status --short"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_allows_git_config_override_log():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -c user.name=x log"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_allows_git_git_dir_spaced_status():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git --git-dir /tmp/.git status"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_allows_git_work_tree_spaced_diff():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git --work-tree /tmp diff"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_allows_git_git_dir_glued_status():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git --git-dir=/tmp/.git status"}, "orchestrator"
    )
    assert allow is True


def test_orchestrator_denies_git_dash_c_path_commit():
    # The value-skipping must resolve the subcommand, not lose it.
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -C /tmp commit -m x"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_dash_c_path_push():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -C /tmp push"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_config_override_worktree_add():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git -c x=y worktree add /tmp/w"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_git_git_dir_spaced_commit():
    allow, _ = role_gate.decide(
        "Bash", {"command": "git --git-dir /tmp/.git commit -m x"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_unknown_global_flag_before_commit():
    # An unknown flag must NOT eat the next token — otherwise ``commit`` would
    # be skipped over and the command would resolve to ``-m`` and slip through.
    allow, _ = role_gate.decide(
        "Bash", {"command": "git --foo commit -m x"}, "orchestrator"
    )
    assert allow is False


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
    assert "голове (orchestrator)" in reason


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
# Orchestrator — Bash: pipes and chains, allowed link by link
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git status | head -5",
    "ls | wc -l",
    "cd /tmp && grep x y",
    "tide status | tail -3",
    "grep x y 2>&1",
    "git log --oneline | head -20 | sort",
    "cat a.txt | grep foo | wc -l",
    "pwd; ls -la",
    "tide status 2>&1 | tail -3",
    'grep "a|b" file.txt',  # operator inside quotes is not a separator
])
def test_orchestrator_allows_readonly_pipeline(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    "ls | rm -rf .",           # mutating tail of a pipe
    "cat a | sh",              # shelling out
    "cd /tmp && rm -rf .",     # mutating link of a chain
    "git status; git commit -m x",
    "ls | tee out.txt",        # tee writes, and is not on the list
    "ls & rm -rf .",           # backgrounding is a separator too
])
def test_orchestrator_denies_pipeline_with_mutating_link(command):
    allow, reason = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False
    assert "голове (orchestrator)" in reason


@pytest.mark.parametrize("command", [
    "echo $(rm -rf .)",
    "echo `rm -rf .`",
    "ls ${HOME}",
    "cat <(rm -rf .)",
    "ls | grep $(whoami)",
])
def test_orchestrator_denies_command_substitution(command):
    """A subshell hides its command from the allowlist — deny the whole segment."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False


@pytest.mark.parametrize("command", [
    'grep "a | b',      # unterminated double quote
    "grep 'a | b",      # unterminated single quote
])
def test_orchestrator_denies_unbalanced_quotes(command):
    """Unparseable quoting must fail towards deny, never towards allow."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False


# ---------------------------------------------------------------------------
# Orchestrator — read-only shapes the gate used to cut for nothing (cand 155)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "tide status 2>/dev/null",
    "git status 2> /dev/null",          # spaced form
    "ls -la 2>/dev/null | head -5",
    "tide status 2>&1 2>/dev/null",
])
def test_orchestrator_allows_stderr_discard(command):
    """``2>/dev/null`` throws stderr away; it writes no file."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    "ls || echo none",
    "tide handoffs take || tide status",
    "grep -c x file.txt 2>/dev/null || echo 0",
    "cd /tmp && ls || echo empty",
])
def test_orchestrator_allows_or_chain_of_readonly_links(command):
    """A fallback chain is worth exactly what its links are — same as ``&&``."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    'tide candidate add "голова видит <скобки> и стрелку -> вот так"',
    "tide candidate add \"он сказал 'привет' и ушёл\"",
    'grep "a > b" file.txt',
    "echo 'literal $(rm -rf x) text'",  # single quotes suppress the expansion
])
def test_orchestrator_allows_syntax_characters_inside_quoted_arguments(command):
    """Quoted text is an argument. The gate used to read ``>`` in it as a redirect."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    "tide status 2>err.log",            # stderr into a FILE is still a write
    "echo hi 2>>err.log",
    "ls 2>/dev/null > out.txt",         # the discard must not cover a real redirect
    "ls 2>&1 > out.txt",
    'echo "$(rm -rf x)"',               # double quotes do not stop substitution
    'echo "${HOME}"',
    "ls || rm -rf x",                   # a bad link poisons an or-chain too
    "rm -rf x || echo ok",
])
def test_orchestrator_still_denies_writes_and_substitution(command):
    """The softening is read-only shapes only — nothing that writes or hides a command."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False


def test_orchestrator_denies_redirect_inside_pipeline():
    allow, _ = role_gate.decide(
        "Bash", {"command": "ls | head -3 > out.txt"}, "orchestrator"
    )
    assert allow is False


def test_orchestrator_denies_tide_with_redirect():
    """``tide`` no longer buys an exemption from the redirect rule."""
    allow, _ = role_gate.decide(
        "Bash", {"command": "tide status > out.txt"}, "orchestrator"
    )
    assert allow is False


# ---------------------------------------------------------------------------
# Orchestrator — a newline separates commands exactly like `;` does
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "ls\nrm -rf x",
    "git status\ngit commit -m x",
    "tide status\npip install requests",
    "ls\r\nrm -rf x",
])
def test_orchestrator_denies_mutating_link_on_a_second_line(command):
    """A multi-line Bash command is several commands — every line has to earn its way in.

    This used to be the way through the gate: the splitter knew ``;`` but not the
    newline, so ``ls\\nrm -rf x`` was ONE link whose first token was ``ls``, and
    the whole thing was waved past. Found by the manikin (work 38) while probing.
    """
    allow, reason = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False
    assert "голове (orchestrator)" in reason


@pytest.mark.parametrize("command", [
    "ls -la\ngit status",
    "tide status\ntide handoffs",
    "git log --oneline \\\n  --graph",   # escaped newline = line continuation
])
def test_orchestrator_allows_multi_line_readonly_command(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


def test_deny_message_names_what_is_allowed():
    """The refusal has to teach the way out, not just close the door."""
    _, reason = role_gate.decide("Bash", {"command": "rm -rf ."}, "orchestrator")
    assert "Тебе доступно" in reason
    assert "через Agent" in reason
    assert "гард его не держит" in reason
    # The list has to name what the gate actually lets through, or the head
    # keeps rediscovering `||` and `2>/dev/null` by getting refused.
    assert role_gate.ALLOWED_SURFACE in reason
    assert "||" in role_gate.ALLOWED_SURFACE
    assert "2>/dev/null" in role_gate.ALLOWED_SURFACE


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
# Subagents carry worker capability despite inheriting the head's role
# ---------------------------------------------------------------------------

def test_subagent_allows_write_despite_orchestrator_role():
    allow, reason = role_gate.decide(
        "Write", {"file_path": "/tmp/x.py", "content": "x"},
        "orchestrator", is_subagent=True,
    )
    assert allow is True
    assert reason == ""


def test_subagent_allows_mutating_bash_despite_orchestrator_role():
    allow, _ = role_gate.decide(
        "Bash", {"command": "rm -rf ."}, "orchestrator", is_subagent=True,
    )
    assert allow is True


def test_head_still_denied_when_not_a_subagent():
    """The default (is_subagent=False) must keep the head empty-handed."""
    allow, reason = role_gate.decide(
        "Write", {"file_path": "/tmp/x.py", "content": "x"}, "orchestrator",
    )
    assert allow is False
    assert "голове (orchestrator)" in reason


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
    assert "голове (orchestrator)" in err


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
    assert "голове (orchestrator)" in capsys.readouterr().err


def test_cli_allows_tide_bash_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"tide status"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_git_status_for_orchestrator(monkeypatch, capsys):
    payload = '{"tool_name":"Bash","tool_input":{"command":"git status"}}'
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_write_when_payload_carries_agent_id(monkeypatch, capsys):
    """agent_id in the payload means the call came from inside a subagent."""
    payload = (
        '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x.py","content":"x"},'
        '"agent_id":"a02847724799915bc","agent_type":"Explore"}'
    )
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_allows_mutating_bash_when_payload_carries_agent_id(monkeypatch, capsys):
    payload = (
        '{"tool_name":"Bash","tool_input":{"command":"rm -rf ."},'
        '"agent_id":"a02847724799915bc"}'
    )
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.ALLOW_EXIT


def test_cli_denies_write_when_agent_id_is_blank(monkeypatch, capsys):
    """An empty/whitespace agent_id is not a subagent — the head stays blocked."""
    payload = (
        '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x.py","content":"x"},'
        '"agent_id":"   "}'
    )
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT
    assert "голове (orchestrator)" in capsys.readouterr().err


def test_cli_denies_write_when_agent_id_is_not_a_string(monkeypatch, capsys):
    """A garbled agent_id must not be mistaken for a subagent."""
    payload = (
        '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x.py","content":"x"},'
        '"agent_id":123}'
    )
    rc = _run_role_gate(monkeypatch, payload)
    assert rc == role_gate.BLOCK_EXIT


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
# The manikin — ask the gate how it would judge, run nothing (cand 166)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "tide status",
    "ls | head -3",
    "rm -rf x",
    "echo hi > out.txt",
    'echo "$(rm -rf x)"',
    "ls\nrm -rf x",
    'grep "a | b',
    "",
])
def test_explain_verdict_always_matches_the_live_gate(command):
    """One truth: the probe reads the gate's own decision, it does not re-implement it."""
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    text = role_gate.explain(command)
    assert ("ALLOW" in text) is allow
    assert ("DENY" in text) is not allow
    assert "nothing ran" in text


def test_explain_names_the_refusing_link_and_the_rule():
    text = role_gate.explain("ls | rm -rf x")
    assert "link 2 of 2" in text
    assert "`rm -rf x`" in text
    assert "нет в списке чтения" in text
    assert role_gate.ALLOWED_SURFACE in text


@pytest.mark.parametrize("command,rule_fragment", [
    ('echo "$(rm -rf x)"', "подстановка команды"),
    ("cat <(rm -rf x)", "подстановка процесса"),
    ("echo hi > out.txt", "перенаправление в файл"),
    ("git commit -m x", "git не на чтение"),
    ("pip install requests", "нет в списке чтения"),
    ('grep "a | b', "незакрытые кавычки"),
])
def test_explain_names_the_rule_that_refused(command, rule_fragment):
    assert rule_fragment in role_gate.explain(command)


def test_explain_lists_the_links_it_would_let_through():
    text = role_gate.explain("git status | head -5")
    assert "`git status`" in text
    assert "`head -5`" in text


def test_explain_renders_a_multi_line_command_as_a_block():
    """A newline in the command must not garble the report it is reported in."""
    text = role_gate.explain("ls -la\nrm -rf x")
    assert "  command:\n    ls -la\n    rm -rf x" in text
    assert "link 2 of 2" in text


# --- the manikin through the CLI -------------------------------------------

def test_cli_explain_prints_the_verdict_and_exits_zero(capsys):
    rc = cli.main(["hook", "role-gate", "--explain", "ls | head -3"])
    assert rc == role_gate.ALLOW_EXIT
    assert "ALLOW" in capsys.readouterr().out


def test_cli_explain_file_reads_the_command_from_disk(tmp_path, capsys):
    probe = tmp_path / "probe.txt"
    probe.write_text("rm -rf x\n", encoding="utf-8")  # trailing newline is the file's
    rc = cli.main(["hook", "role-gate", "--explain-file", str(probe)])
    assert rc == role_gate.ALLOW_EXIT
    out = capsys.readouterr().out
    assert "DENY" in out
    assert "link 1 of 1" in out


def test_cli_explain_file_leaves_the_dangerous_string_a_string(tmp_path, capsys):
    """The whole reason this verb exists: probing must not cost an execution.

    31.07, candidate 166: a worker checking these very rules typed ``$(rm -rf .)``
    into a live Bash and the substitution ran — the tree survived on luck. The
    probe below carries BOTH a substitution and a redirect that would each leave
    a mark on disk; the marker must not exist afterwards.
    """
    marker = tmp_path / "SHOULD-NOT-EXIST"
    probe = tmp_path / "probe.txt"
    probe.write_text(
        'echo "$(touch {0})" > {0}\n'.format(marker), encoding="utf-8"
    )

    rc = cli.main(["hook", "role-gate", "--explain-file", str(probe)])

    assert rc == role_gate.ALLOW_EXIT
    assert not marker.exists()
    out = capsys.readouterr().out
    assert "DENY" in out
    assert "nothing ran" in out


def test_cli_explain_rejects_both_sources_at_once(tmp_path, capsys):
    rc = cli.main(["hook", "role-gate", "--explain", "ls", "--explain-file", str(tmp_path)])
    assert rc == 1
    assert "not both" in capsys.readouterr().err


def test_cli_explain_file_reports_an_unreadable_path(tmp_path, capsys):
    rc = cli.main(["hook", "role-gate", "--explain-file", str(tmp_path / "nope.txt")])
    assert rc == 1
    assert "cannot read" in capsys.readouterr().err


def test_cli_explain_does_not_touch_stdin(monkeypatch, capsys):
    """The manikin answers from its argument — it must not block on the payload channel."""
    monkeypatch.setattr("sys.stdin", None)  # any read would raise
    rc = cli.main(["hook", "role-gate", "--explain", "tide status"])
    assert rc == role_gate.ALLOW_EXIT
    assert "ALLOW" in capsys.readouterr().out


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
    # Eight notes on first install: SessionStart + edit-gate + role-gate + ask-gate +
    # handoff-confirm + Stop offload-nudge + Stop context-watch + SessionEnd.
    assert len(notes) == 8


# ---------------------------------------------------------------------------
# Orchestrator — AO session reads and the manager's Linear tool (decision № 26)
# ---------------------------------------------------------------------------

_LAM = role_gate._LINEAR_AS_MANAGER


@pytest.mark.parametrize("command", [
    "ao session ls",
    "ao session ls --json",
    "ao session show tide-stack-12",
    "ao session show tide-stack-12 | head -20",
    "ao session ls && tide status",
    _LAM,
    _LAM + " issue SOC-101",
    _LAM + " comment SOC-101 'готово → ревью'",
    "'" + _LAM + "' issue SOC-101",
    _LAM + " issue SOC-101 2>&1 | tail -5",
])
def test_orchestrator_allows_ao_reads_and_linear_as_manager(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    "ao",
    "ao spawn tide-stack",
    "ao session kill tide-stack-12",
    "ao session",
    "ao --verbose session ls",
    "ao session restore tide-stack-12",
    "ao status",
    "ao session ls; ao session kill x",
    "/tmp/linear-as-manager issue SOC-101",
    "linear-as-manager issue SOC-101",
    "./linear-as-manager issue SOC-101",
    "~/Documents/management/tools/linear-as-manager issue SOC-101",
    _LAM.replace("/tools/", "/tools/../tools/") + " x",
    _LAM + ".bak issue SOC-101",
    "bash -c '" + _LAM + " issue SOC-101'",
    "sh " + _LAM,
    _LAM + " issue SOC-101 > out.txt",
    _LAM + " $(rm -rf .)",
])
def test_orchestrator_denies_other_ao_and_linear_paths(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False


@pytest.mark.parametrize("command", [
    "ao send tide-stack-12 'привет'",
    "ao send tide-stack-12 --file /tmp/msg.md",
    "ao session ls && ao send tide-stack-12 'проверь SOC-101'",
])
def test_orchestrator_allows_ao_send(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True


@pytest.mark.parametrize("command", [
    "ao send x 'hi' > /tmp/log",
    "ao send x \"$(cat /etc/passwd)\"",
    "ao send x 'hi'; ao session kill x",
    "ao sendall x",
])
def test_ao_send_does_not_open_the_rest(command):
    allow, _ = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False


def test_explain_names_the_ao_rule():
    text = role_gate.explain("ao session kill tide-stack-12")
    assert "DENY" in text
    assert "ao session ls|show" in text
    assert "ao send" in text



# ---------------------------------------------------------------------------
# Soft hint: ao send is a signal, not a letter (window cut п.3, tg 1223)
# ---------------------------------------------------------------------------

def _ao_send_payload(text: str) -> str:
    import json
    cmd = "ao send --session tide-stack-12 --message " + __import__("shlex").quote(text)
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})


@pytest.mark.parametrize("role", ["orchestrator", "worker"])
def test_long_ao_send_passes_with_a_hint(monkeypatch, capsys, role):
    import json
    from tide import escalate_rules

    text = "я" * (escalate_rules.AO_SEND_SIGNAL_CHARS + 1)
    rc = _run_role_gate(monkeypatch, _ao_send_payload(text), role=role)
    assert rc == role_gate.ALLOW_EXIT  # never a refusal
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out  # no decision — the normal flow stays
    hint = out["additionalContext"]
    assert "{0} знаков".format(len(text)) in hint
    assert "только сигнал" in hint and "tide mail send <проект>/<нить>" in hint


def test_short_ao_send_is_silent(monkeypatch, capsys):
    from tide import escalate_rules

    text = "письмо 007 в почте нити"
    assert len(text) <= escalate_rules.AO_SEND_SIGNAL_CHARS
    rc = _run_role_gate(monkeypatch, _ao_send_payload(text))
    assert rc == role_gate.ALLOW_EXIT
    assert capsys.readouterr().out == ""


def test_hint_only_for_ao_send():
    long = "x" * 1000
    assert role_gate.ao_send_hint("Bash", {"command": "echo " + long}) is None
    assert role_gate.ao_send_hint("Bash", {"command": "ao session ls " + long}) is None
    assert role_gate.ao_send_hint("Read", {"command": "ao send --message " + long}) is None
    assert role_gate.ao_send_chars("ao send --session s --message=" + long) == 1000
    assert role_gate.ao_send_chars("tide status && ao send --session s --message '"
                                   + long + "'") == 1000


# ---------------------------------------------------------------------------
# SOC-181 п.12: the head reads the clock and slices text itself — date, cut, tr,
# jq, sed -n, awk — while anything that writes or hides a command stays denied.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "date",
    "date +%H:%M",
    "date -u '+%Y-%m-%d %H:%M'",
    'date -d "-2 days" +%F',
    "date -j -f %Y 2026 +%s",
    "cut -d, -f1 a.csv",
    "echo abc | tr a-z A-Z",
    "jq . x.json",
    "jq -r '.a[] | .b' x.json",
    "sed -n '1,5p' f.txt",
    "sed -n '/start/,/end/p' f.txt",
    "sed -n 's/a/b/p' f.txt",
    "sed -n -e '1p' -e '$p' f.txt",
    "sed -nE '/x|y/p' f.txt",
    "sed -n ':a;N;$!ba;s/\\n/ /gp' f.txt",
    "awk '{print $1}' f.txt",
    "awk -F: '$3 > 100 {print $1}' /etc/passwd",
    "awk 'NR==1 || /x|y/ {print}' f.txt",
    "awk -v n=3 'NR<=n' f.txt",
    "git log --oneline | head -5 | awk '{print $1}'",
    "cat f.txt | sed -n '2p' | cut -c1-10",
])
def test_orchestrator_allows_readonly_text_tools(command):
    allow, reason = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True, reason


@pytest.mark.parametrize("command,rule", [
    ("sed -i 's/a/b/' f.txt", "sed голове"),
    ("sed -n -i 's/a/b/' f.txt", "sed голове"),
    ("sed -ni '1p' f.txt", "sed голове"),
    ("sed -n --in-place '1p' f.txt", "sed голове"),
    ("sed 's/a/b/' f.txt", "sed голове"),               # without -n
    ("sed -n 'w out' f.txt", "sed голове"),
    ("sed -n '1w out' f.txt", "sed голове"),
    ("sed -n 's/a/b/w out' f.txt", "sed голове"),
    ("sed -n 'W out' f.txt", "sed голове"),
    ("sed -n 'e ls' f.txt", "sed голове"),
    ("sed -n 's/a/b/e' f.txt", "sed голове"),
    ("sed -n -f script.sed f.txt", "sed голове"),
    ("awk '{print $1 > \"out\"}' f.txt", "awk голове"),
    ("awk '{print > \"/tmp/x\"}' f.txt", "awk голове"),
    ("awk '{printf \"%s\", $1 >> \"o\"}' f.txt", "awk голове"),
    ("awk 'BEGIN{system(\"rm -rf x\")}'", "awk голове"),
    ("awk '{print | \"sh\"}' f.txt", "awk голове"),
    ("awk 'BEGIN{\"date\" | getline d; print d}'", "awk голове"),
    ("awk -f prog.awk f.txt", "awk голове"),
    ("awk '@load \"filefuncs\"; BEGIN{}'", "awk голове"),
    ("date -s 12:00", "date —"),
    ("date 1011120026", "date —"),
    ("date --set=now", "date —"),
    ("echo $(date)", "подстановка команды"),
    ("ls `date`", "подстановка команды"),
    ("date > now.txt", "перенаправление в файл"),
    ("sed -n '1p' f.txt > out.txt", "перенаправление в файл"),
])
def test_orchestrator_denies_writing_text_tools(command, rule):
    allow, reason = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is False
    assert rule in reason


def test_refusal_is_two_lines_what_and_how():
    """SOC-181: line 1 — what was refused (the link and the rule), line 2 — the way
    out: hand it to a helper, plus the list of what the head may do."""
    _, reason = role_gate.decide("Bash", {"command": "sed -i 's/a/b/' f.txt"}, "orchestrator")
    lines = reason.split("\n")
    assert len(lines) == 2
    assert "это нельзя" in lines[0] and "`sed -i 's/a/b/' f.txt`" in lines[0]
    assert "отдай её через Agent" in lines[1] and role_gate.ALLOWED_SURFACE in lines[1]
    _, reason = role_gate.decide("Write", {"file_path": "/tmp/x"}, "orchestrator")
    assert len(reason.split("\n")) == 2


# SOC-271 / SOC-270: голове разрешены быстрые команды менеджера
@pytest.mark.parametrize("command", [
    'tide say "x"',
    "tide say --file /tmp/a.png --reply-to 12 'текст'",
    "tide topic ls",
    "tide topic new topics Тест --to management/signing",
    "tide topic close topics Тест",
    "tide spark management --new-thread x --topic --dry-run",
])
def test_orchestrator_allows_manager_quick_commands(command):
    allow, reason = role_gate.decide("Bash", {"command": command}, "orchestrator")
    assert allow is True, reason

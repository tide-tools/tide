"""tide's hooks reach an AO session worktree whose ``.claude`` is in git.

AO cuts the worktree from the COMMITTED repo; a tracked ``.claude`` can't be
symlinked, so the hooks merged into the main tree never reach it (live 09.10:
music-3..7 without context-watch, mitehq-1 without tide hooks, nochnaya-smena-1
without settings.json). The project's AO ``postCreate`` runs
``tide install-hooks --worktree`` in the fresh worktree before the agent starts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tide import cli, harness
from tide.adapters import ao

from tests.test_ao_adapter import _git_repo


def _git(path, *a):
    return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True,
                          text=True).stdout


def _tracked_claude_repo(tmp_path) -> Path:
    """A repo with .claude/settings.json in git (music/mitehq shape) + an AO worktree."""
    repo = _git_repo(tmp_path / "music", ignore_claude=False)
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls)"]}}) + "\n")
    _git(repo, "commit", "-qam", "own settings")
    wt = tmp_path / "ao" / "music-3"
    _git(repo, "worktree", "add", "-q", "-b", "session/music-3", str(wt))
    return wt


def _commands(path: Path):
    data = json.loads(path.read_text())
    return {h["command"] for groups in data["hooks"].values() for g in groups
            for h in g["hooks"]}


# --- the merge into the worktree --------------------------------------------------

def test_worktree_gets_tide_hooks_merged_and_never_committed(tmp_path):
    wt = _tracked_claude_repo(tmp_path)
    path, notes = harness.install_hooks_in_worktree(wt)
    assert path == wt / ".claude" / "settings.json" and notes
    assert {harness.CONTEXT_WATCH_CMD, harness.HANDOFF_CONFIRM_CMD,
            harness.SESSION_START_CMD} <= _commands(path)
    assert json.loads(path.read_text())["permissions"] == {"allow": ["Bash(ls)"]}
    assert _git(wt, "status", "--porcelain") == ""  # skip-worktree: nothing to commit
    assert harness.install_hooks_in_worktree(wt)[1] == []  # idempotent


def test_worktree_without_settings_json_gets_one(tmp_path):
    repo = _git_repo(tmp_path / "noch", claude_dir=False, ignore_claude=False)
    path, notes = harness.install_hooks_in_worktree(repo)
    assert path.is_file() and harness.SESSION_START_CMD in _commands(path)


def _linked_worktree(tmp_path, settings: dict):
    main = tmp_path / "main" / ".claude"
    main.mkdir(parents=True)
    (main / "settings.json").write_text(json.dumps(settings) + "\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".claude").symlink_to(main)
    return main / "settings.json", wt


def test_symlinked_claude_with_full_set_is_left_alone(tmp_path):
    full: dict = {}
    harness.merge_hooks(full)
    main, wt = _linked_worktree(tmp_path, full)
    before = main.read_text()
    assert harness.missing_hooks_at(wt / ".claude" / "settings.json") == []
    path, notes = harness.install_hooks_in_worktree(wt)
    assert path.resolve() == main.resolve() and notes == []
    assert main.read_text() == before
    assert not main.with_name("settings.json.bak").exists()


def test_symlinked_claude_missing_hook_is_merged_into_the_main_tree(tmp_path):
    """SOC-101 cand 276: a link is not proof — ask-gate missing in the main tree."""
    have: dict = {"permissions": {"allow": ["Bash(ls)"]},
                  "hooks": {"PreToolUse": [{"matcher": "Bash",
                                            "hooks": [{"type": "command", "command": "rtk"}]}]}}
    harness.merge_hooks(have)
    have["hooks"]["PreToolUse"] = [g for g in have["hooks"]["PreToolUse"]
                                   if g.get("matcher") != harness.ASK_GATE_MATCHER]
    main, wt = _linked_worktree(tmp_path, have)
    before = main.read_text()
    assert harness.missing_hooks_at(wt / ".claude" / "settings.json") == ["ask-gate"]
    path, notes = harness.install_hooks_in_worktree(wt)
    assert path.resolve() == main.resolve() and len(notes) == 1 and "ask-gate" in notes[0]
    data = json.loads(main.read_text())
    assert harness.ASK_GATE_CMD in _commands(main) and "rtk" in _commands(main)
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert (wt / ".claude").is_symlink()  # the link stays a link
    assert main.with_name("settings.json.bak").read_text() == before
    assert harness.install_hooks_in_worktree(wt)[1] == []  # idempotent


def test_cli_worktree_on_a_link_names_what_it_merged(tmp_path, monkeypatch, capsys):
    main, wt = _linked_worktree(tmp_path, {})
    monkeypatch.chdir(wt)
    assert cli.main(["install-hooks", "--worktree"]) == 0
    out = capsys.readouterr().out
    assert "hooks merged into" in out and "ask-gate" in out
    assert cli.main(["install-hooks", "--worktree"]) == 0
    assert "all hooks already in" in capsys.readouterr().out


def test_cli_worktree_wires_cwd_not_the_main_tree(tmp_path, monkeypatch, capsys):
    wt = _tracked_claude_repo(tmp_path)
    monkeypatch.chdir(wt)
    assert cli.main(["install-hooks", "--worktree"]) == 0
    assert harness.CONTEXT_WATCH_CMD in _commands(wt / ".claude" / "settings.json")
    main_settings = tmp_path / "music" / ".claude" / "settings.json"
    assert "hooks" not in json.loads(main_settings.read_text())
    assert "hooks wired" in capsys.readouterr().out


def test_cli_worktree_never_fails_the_spawn(tmp_path, monkeypatch, capsys):
    def boom(_tree):
        raise OSError("read-only")

    monkeypatch.setattr("tide.hooks.install.install_hooks_in_worktree", boom)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["install-hooks", "--worktree"]) == 0
    assert "skipped: read-only" in capsys.readouterr().err


# --- the AO project config --------------------------------------------------------

def test_tracked_claude_gets_a_post_create_step_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    repo = _git_repo(tmp_path / "music", ignore_claude=False)
    cur = {"postCreate": ["npm install"]}
    cfg = ao.desired_config(cur, tree=repo, control_home=None, role=None,
                            skip_permissions=False)
    assert cfg["postCreate"] == ["npm install", "/bin/tide install-hooks --worktree"]
    again = ao.desired_config(cfg, tree=repo, control_home=None, role=None,
                              skip_permissions=False)
    assert again == cfg
    assert "symlinks" not in cfg


def test_symlinked_claude_needs_no_post_create(tmp_path, monkeypatch):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    repo = _git_repo(tmp_path / "mgmt")  # .claude untracked → symlinked
    cfg = ao.desired_config({}, tree=repo, control_home=None, role=None,
                            skip_permissions=False)
    assert cfg["symlinks"] == [".claude"] and "postCreate" not in cfg


def test_no_tide_on_path_adds_no_post_create(tmp_path, monkeypatch):
    monkeypatch.setattr(ao.shutil, "which", lambda name: None)
    repo = _git_repo(tmp_path / "music", ignore_claude=False)
    cfg = ao.desired_config({}, tree=repo, control_home=None, role=None,
                            skip_permissions=False)
    assert "postCreate" not in cfg

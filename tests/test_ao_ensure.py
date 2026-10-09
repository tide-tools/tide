"""SOC-101 п.1: `tide ao ensure` — the postCreate step reaches AO projects whose
sessions are started straight from the AO app, not only through tide's spawn.
Runs against the fake `ao` (tests.test_ao_adapter) — never the real app."""

from __future__ import annotations

import json

from tide import cli
from tide.adapters import ao

from tests.test_ao_adapter import _git_repo, fake_ao  # noqa: F401 — fixture

LIVE_CFG = {"containerReap": {}, "env": {"TIDE_HOME": "/home", "TIDE_ROLE": "orchestrator",
                                         "TIDE_DIALOG_LOG": "/d.jsonl"},
            "symlinks": [".tide"], "agentConfig": {"permissions": "bypass-permissions"},
            "worker": {"agent": "claude-code", "agentConfig": {}}}


def _seed(fake_read, tmp_path, projects):
    """Write fake-AO state: {pid: (path, config)}."""
    state = {"projects": {pid: {"path": str(path), "name": pid, "config": cfg}
                          for pid, (path, cfg) in projects.items()},
             "sessions": {}, "calls": []}
    (tmp_path / "ao-state.json").write_text(json.dumps(state))


def _music(tmp_path):
    return _git_repo(tmp_path / "music", ignore_claude=False)  # tracked .claude


def test_ensure_merges_post_create_keeping_env_and_agent_config(fake_ao, tmp_path,
                                                                monkeypatch, capsys):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG)})
    assert cli.main(["ao", "ensure", "music"]) == 0
    cfg = fake_ao()["projects"]["music"]["config"]
    assert cfg["postCreate"] == ["/bin/tide install-hooks --worktree"]
    for key in ("env", "agentConfig", "worker", "symlinks", "containerReap"):
        assert cfg[key] == LIVE_CFG[key]
    assert "added postCreate" in capsys.readouterr().out
    # idempotent: the second run writes nothing
    calls = len(fake_ao()["calls"])
    assert cli.main(["ao", "ensure", "music"]) == 0
    new_calls = fake_ao()["calls"][calls:]
    assert not any(c[:2] == ["project", "set-config"] for c in new_calls)
    assert "already reach" in capsys.readouterr().out


def test_dry_run_writes_nothing(fake_ao, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG)})
    assert cli.main(["ao", "ensure", "music", "--dry-run"]) == 0
    assert "would add postCreate" in capsys.readouterr().out
    assert fake_ao()["projects"]["music"]["config"] == LIVE_CFG


def test_all_touches_only_tide_projects(fake_ao, tmp_path, monkeypatch):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG), "plain": (plain, {"env": {}})})
    assert cli.main(["ao", "ensure", "--all", "--json"]) == 0
    st = fake_ao()["projects"]
    assert "postCreate" in st["music"]["config"]
    assert st["plain"]["config"] == {"env": {}}


def test_ensure_by_path_and_unknown_project(fake_ao, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG)})
    assert cli.main(["ao", "ensure", str(music)]) == 0
    assert "postCreate" in fake_ao()["projects"]["music"]["config"]
    assert cli.main(["ao", "ensure", "nope"]) == 1
    assert "not an AO project" in capsys.readouterr().err


def test_write_config_reports_keys_ao_dropped(monkeypatch):
    calls = []
    monkeypatch.setattr(ao, "_call", lambda args, **k: calls.append(args) or "")
    monkeypatch.setattr(ao, "_json", lambda args, **k: {
        "project": {"config": {"postCreate": ["x"]}}})
    notes = ao.write_config("music", {"postCreate": ["x"], "env": {"A": "1"}})
    assert calls[0][:3] == ["project", "set-config", "music"]
    assert json.loads(calls[0][4]) == {"postCreate": ["x"], "env": {"A": "1"}}
    assert notes == ["music: AO did not keep env"]


def test_install_hooks_in_main_tree_ensures_ao(fake_ao, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG)})
    monkeypatch.chdir(music)
    assert cli.main(["install-hooks"]) == 0
    assert "postCreate" in fake_ao()["projects"]["music"]["config"]
    assert "AO music" in capsys.readouterr().out


def test_install_hooks_without_ao_is_quiet(tmp_path, monkeypatch, capsys):
    music = _music(tmp_path)
    monkeypatch.chdir(music)
    assert cli.main(["install-hooks"]) == 0
    assert "AO" not in capsys.readouterr().out


# --- doctor F9 names the cause: the AO config without postCreate ------------------

def test_doctor_f9_names_unwired_ao_projects(fake_ao, tmp_path, monkeypatch):
    from tide import doctor

    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _music(tmp_path)
    _seed(fake_ao, tmp_path, {"music": (music, LIVE_CFG)})
    assert doctor.ao_projects_without_hooks() == ["music"]
    res = doctor.check_ao_worktree_hooks(tmp_path / "no-worktrees")
    assert res.status == doctor.STATUS_WARN
    assert "music" in res.detail and "tide ao ensure --all" in res.detail
    assert cli.main(["ao", "ensure", "--all"]) == 0
    assert doctor.ao_projects_without_hooks() == []
    assert doctor.check_ao_worktree_hooks(tmp_path / "no-worktrees").status == doctor.STATUS_OK


def test_doctor_f9_without_ao_says_nothing_extra(tmp_path):
    from tide import doctor

    assert doctor.ao_projects_without_hooks() == []
    assert doctor.check_ao_worktree_hooks(tmp_path / "none").status == doctor.STATUS_OK

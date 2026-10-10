"""SOC-98: AO-копии без хука context-watch не остаются незамеченными, а запись
настроек проекта AO не теряет effort. Только tmp-дерево и поддельный `ao`."""

from __future__ import annotations

import json

from tide import cli, doctor
from tide.adapters import ao
from tide.hooks import install

from tests.test_ao_adapter import _git_repo, fake_ao  # noqa: F401 — fixture
from tests.test_ao_ensure import _seed


def _wt(root, project, session):
    wt = root / project / session
    (wt / ".claude").mkdir(parents=True)
    (wt / ".tide").mkdir()
    return wt


def _drop_context_watch(wt):
    path = install.settings_path(wt)
    data = json.loads(path.read_text(encoding="utf-8"))
    for ev, groups in list(data["hooks"].items()):
        for g in groups:
            g["hooks"] = [h for h in g["hooks"] if "context-watch" not in h["command"]]
    path.write_text(json.dumps(data), encoding="utf-8")


def test_doctor_names_worktree_missing_only_context_watch(tmp_path):
    root = tmp_path / "worktrees"
    good = _wt(root, "nochnaya-smena", "nochnaya-smena-2")
    bad = _wt(root, "nochnaya-smena", "nochnaya-smena-4")
    install.install_hooks(good)
    install.install_hooks(bad)
    _drop_context_watch(bad)
    res = doctor.check_ao_worktree_hooks(root, unwired=[], linked=[], terminated=set())
    assert res.status == doctor.STATUS_WARN          # предупреждение, не ошибка
    assert "nochnaya-smena/nochnaya-smena-4 (нет: context-watch)" in res.detail
    assert "nochnaya-smena-2" not in res.detail
    assert "tide install-hooks --worktree" in res.detail
    assert doctor.DoctorReport([res]).exit_code == 0


def test_doctor_linked_claude_with_hooks_is_fine_without_is_not(tmp_path):
    main = tmp_path / "main"
    (main / ".claude").mkdir(parents=True)
    install.install_hooks(main)
    root = tmp_path / "worktrees"
    wt = root / "management" / "management-5"
    (wt / ".tide").mkdir(parents=True)
    (wt / ".claude").symlink_to(main / ".claude")
    kw = dict(unwired=[], linked=[], terminated=set())
    assert doctor.check_ao_worktree_hooks(root, **kw).status == doctor.STATUS_OK
    _drop_context_watch(main)                         # ссылка не доказательство
    res = doctor.check_ao_worktree_hooks(root, **kw)
    assert res.status == doctor.STATUS_WARN
    assert "management/management-5 (нет: context-watch)" in res.detail


def test_doctor_skips_terminated_and_plain_dirs(tmp_path):
    root = tmp_path / "worktrees"
    dead = _wt(root, "music", "music-4")
    (root / "music" / "orchestrator").mkdir()         # не tide-копия — не судим
    res = doctor.check_ao_worktree_hooks(root, unwired=[], linked=[], terminated={dead.name})
    assert res.status == doctor.STATUS_OK


EFFORT_CFG = {
    "containerReap": {},
    "env": {"TIDE_HOME": "/home"},
    "symlinks": [".tide"],
    "agentConfig": {"permissions": "bypass-permissions", "effort": "high"},
    "worker": {"agent": "claude-code", "agentConfig": {"effort": "xhigh", "model": "opus"}},
    "orchestrator": {"agentConfig": {"effort": "max"}},
}


def test_hooks_and_desired_config_keep_effort_everywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    tree = _git_repo(tmp_path / "music", ignore_claude=False)
    cfg = ao.hooks_config(EFFORT_CFG, tree=tree)
    assert cfg["postCreate"] == ["/bin/tide install-hooks --worktree"]
    for key in ("agentConfig", "worker", "orchestrator"):
        assert cfg[key] == EFFORT_CFG[key]
    cfg = ao.desired_config(EFFORT_CFG, tree=tree, control_home="/h", role="orchestrator",
                            skip_permissions=True)
    assert cfg["agentConfig"] == {"permissions": "bypass-permissions", "effort": "high"}
    assert cfg["worker"] == EFFORT_CFG["worker"]
    assert cfg["orchestrator"] == EFFORT_CFG["orchestrator"]


def test_ensure_writes_whole_config_with_effort(fake_ao, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ao.shutil, "which", lambda name: "/bin/tide")
    music = _git_repo(tmp_path / "music", ignore_claude=False)
    _seed(fake_ao, tmp_path, {"music": (music, EFFORT_CFG)})
    assert cli.main(["ao", "ensure", "music"]) == 0
    written = fake_ao()["projects"]["music"]["config"]
    assert written["postCreate"] == ["/bin/tide install-hooks --worktree"]
    for key in ("agentConfig", "worker", "orchestrator", "env", "symlinks"):
        assert written[key] == EFFORT_CFG[key]
    assert "did not keep" not in capsys.readouterr().out

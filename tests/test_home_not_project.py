"""Домашняя папка — не tide-проект (edit-gate блокировал репо без своей .tide).

С 09.10 в ``~/.tide`` лежат глобальные файлы (``manager.json`` пульта, ``state/``
perm-notify). Подъём к корню находил ``~/.tide`` — и любой репо под домом без
своей ``.tide`` (studio-games, творческие репо) становился «проектом без
открытой арки»: Edit/Write блокировались. Теперь дом и всё выше — не корень,
а глобальные файлы tide пишет/читает в control-home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import paths, whois
from tide.hooks import edit_gate, perm_notify


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME=tmp с ~/.tide (manager.json + state/), как на живой машине."""
    h = (tmp_path / "home").resolve()
    (h / ".tide" / "state").mkdir(parents=True)
    (h / ".tide" / "manager.json").write_text(
        json.dumps({"ao_session": "management-legacy", "thread": "main"}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("TIDE_HOME", raising=False)
    monkeypatch.delenv("TIDE_MANAGER_FILE", raising=False)
    monkeypatch.delenv("AO_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Path.home().resolve() == h
    return h


def test_repo_under_home_without_tide_is_not_a_project(home):
    repo = home / "Documents" / "projects" / "studio-games"
    (repo / "src").mkdir(parents=True)
    assert paths.find_tide_root(repo) is None
    assert paths.find_tide_root(repo / "src") is None


def test_home_itself_is_not_a_project(home):
    assert paths.find_tide_root(home) is None
    assert paths.not_project_root(home) and paths.not_project_root(home.parent)


def test_edit_gate_allows_edit_in_repo_without_tide(home):
    repo = home / "Documents" / "projects" / "studio-games"
    repo.mkdir(parents=True)
    code, reason = edit_gate.decide(str(repo / "game.ts"), repo)
    assert code == edit_gate.ALLOW and reason == ""


def test_project_with_own_tide_under_home_still_found(home):
    proj = home / "Documents" / "projects" / "app"
    (proj / ".tide" / "arcs").mkdir(parents=True)
    (proj / "src").mkdir()
    assert paths.find_tide_root(proj / "src") == proj
    # гейт по-прежнему сторожит настоящий проект: без открытой арки — блок
    code, reason = edit_gate.decide(str(proj / "src" / "a.py"), proj)
    assert code == edit_gate.BLOCK and "no open arc" in reason


def test_perm_notify_journal_goes_to_control_home_not_home(home, tmp_path, monkeypatch):
    ctl = tmp_path / "tide-home"
    (ctl / ".tide").mkdir(parents=True)
    (ctl / "roster.md").write_text("# roster\n", encoding="utf-8")
    monkeypatch.setenv("TIDE_HOME", str(ctl))
    jp = perm_notify.journal_path(None)
    assert jp == ctl.resolve() / ".tide" / "state" / "blocked-perms.jsonl"


def test_perm_notify_journal_without_control_home_avoids_home(home):
    jp = perm_notify.journal_path(None)
    assert home not in jp.parents


def test_manager_file_prefers_control_home_then_legacy(home, tmp_path, monkeypatch):
    legacy = home / ".tide" / "manager.json"
    monkeypatch.setattr(whois, "MANAGER_FILE_LEGACY", legacy)
    ctl = tmp_path / "tide-home"
    (ctl / ".tide" / "state").mkdir(parents=True)
    (ctl / "roster.md").write_text("# roster\n", encoding="utf-8")
    monkeypatch.setenv("TIDE_HOME", str(ctl))
    # нового файла нет — откат на старый ~/.tide/manager.json (живой пульт)
    assert whois._manager_file() == legacy
    assert whois.manager()["ao_session"] == "management-legacy"
    # новый есть — он главнее
    new = ctl.resolve() / ".tide" / "state" / "manager.json"
    new.write_text(json.dumps({"ao_session": "management-new"}), encoding="utf-8")
    assert whois.manager_file_home() == new
    assert whois._manager_file() == new
    assert whois.manager()["ao_session"] == "management-new"

"""Работа 48 — граница кора и плагинов: каталог, реестр в доме, безопасный дефолт."""

from __future__ import annotations

import pytest

from tide import cli, plugins


@pytest.fixture
def home(tmp_path):
    """A bare control-home: a dir with roster.md and its own .tide/."""
    h = tmp_path / "home"
    (h / ".tide").mkdir(parents=True)
    (h / "roster.md").write_text("# tide roster\n", encoding="utf-8")
    return h


# --- каталог ---------------------------------------------------------------

def test_core_and_plugins_do_not_overlap():
    core = {p.name for p in plugins.CORE}
    plug = {p.name for p in plugins.PLUGINS}
    assert core and plug
    assert not (core & plug)


def test_core_named_by_owner_is_all_there():
    core = {p.name for p in plugins.CORE}
    assert {"projects", "threads", "cli", "board", "system-skills", "hooks"} <= core


def test_plugins_named_by_owner_are_all_there():
    plug = {p.name for p in plugins.PLUGINS}
    assert {"news", "pages", "skills", "issues", "work", "canon"} <= plug


def test_linear_is_a_held_place_not_a_feature():
    p = plugins.part("linear")
    assert p is not None and p.planned and not p.default
    assert "linear" not in plugins.default_enabled()


# --- безопасный дефолт: нет реестра = включено всё -------------------------

def test_no_registry_means_everything_on(home):
    assert not plugins.registry_file(home).exists()
    assert plugins.enabled(home) == plugins.default_enabled()
    assert plugins.is_enabled("news", home)
    assert plugins.is_enabled("pages", home)


def test_garbage_registry_still_reads_as_everything_on(home):
    plugins.registry_file(home).write_text(
        "не файл вовсе\n\x00\xff мусор\n= = =\n", encoding="utf-8")
    assert plugins.enabled(home) == plugins.default_enabled()


def test_unknown_name_is_skipped_not_fatal(home):
    plugins.registry_file(home).write_text(
        "telepathy = off\nnews = off\n", encoding="utf-8")
    on = plugins.enabled(home)
    assert "news" not in on
    assert "pages" in on
    assert plugins.unknown_names(home) == ["telepathy"]


def test_core_cannot_be_switched_off_from_the_file(home):
    plugins.registry_file(home).write_text("board = off\n", encoding="utf-8")
    assert plugins.is_enabled("board", home)
    assert plugins.enabled(home) == plugins.default_enabled()


def test_missing_home_does_not_raise(tmp_path):
    assert plugins.enabled(tmp_path / "nowhere") == plugins.default_enabled()


# --- один жест: включить / выключить ---------------------------------------

def test_set_plugin_off_then_on(home):
    plugins.set_plugin("news", False, home)
    assert "news" not in plugins.enabled(home)
    plugins.set_plugin("news", True, home)
    assert "news" in plugins.enabled(home)


def test_first_switch_writes_the_whole_readable_file(home):
    plugins.set_plugin("news", False, home)
    text = plugins.registry_file(home).read_text(encoding="utf-8")
    for p in plugins.PLUGINS:
        assert "\n{0} = ".format(p.name) in text
    assert "news = off" in text
    assert "# tide plugins" in text


def test_switching_one_leaves_the_others_where_they_were(home):
    plugins.set_plugin("news", False, home)
    plugins.set_plugin("skills", False, home)
    on = plugins.enabled(home)
    assert "news" not in on and "skills" not in on
    assert {"issues", "work", "pages", "canon"} <= on


def test_core_refuses_to_switch(home):
    with pytest.raises(plugins.PluginError):
        plugins.set_plugin("board", False, home)


def test_unknown_refuses_to_switch(home):
    with pytest.raises(plugins.PluginError):
        plugins.set_plugin("telepathy", False, home)


def test_fresh_install_gets_core_only(home):
    plugins.seed_new_install(home)
    assert plugins.enabled(home) == set()
    assert plugins.is_enabled("board", home)  # кор остаётся


# --- CLI -------------------------------------------------------------------

def test_cli_lists_core_and_plugins(home, monkeypatch, capsys):
    monkeypatch.setenv("TIDE_HOME", str(home))
    assert cli.main(["plugins"]) == 0
    out = capsys.readouterr().out
    assert "кор" in out and "съёмное" in out
    assert "news" in out and "board" in out
    assert "нет файла" in out


def test_cli_on_off_round_trip(home, monkeypatch, capsys):
    monkeypatch.setenv("TIDE_HOME", str(home))
    assert cli.main(["plugins", "off", "news"]) == 0
    assert "news" not in plugins.enabled(home)
    assert cli.main(["plugins", "on", "news"]) == 0
    assert "news" in plugins.enabled(home)
    capsys.readouterr()


def test_cli_where_prints_the_path(home, monkeypatch, capsys):
    monkeypatch.setenv("TIDE_HOME", str(home))
    assert cli.main(["plugins", "where"]) == 0
    assert capsys.readouterr().out.strip().endswith("/.tide/plugins")


# --- установка: свежий дом получает кор, старый — не трогается ---------------

def test_fresh_control_home_gets_a_core_only_registry(tmp_path):
    from tide import init_home
    h = tmp_path / "fresh"
    init_home.unfold_control_home(h, name="fresh")
    assert plugins.registry_file(h).is_file()
    assert plugins.enabled(h) == set()


def test_rerunning_init_on_an_existing_home_takes_nothing_away(tmp_path):
    from tide import init_home
    h = tmp_path / "old"
    (h / ".tide").mkdir(parents=True)
    (h / "roster.md").write_text("# tide roster\n", encoding="utf-8")
    init_home.unfold_control_home(h, name="old")
    assert not plugins.registry_file(h).is_file()
    assert plugins.enabled(h) == plugins.default_enabled()

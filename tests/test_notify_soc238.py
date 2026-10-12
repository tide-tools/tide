"""SOC-238: уровень Telegram нити — ``notify: all|questions|quiet`` в паспорте.

Эффективный уровень = самый тихий из своего и всех родительских арок (ребёнок не громче
родителя). По умолчанию all — поведение не меняется. Уровень касается только Telegram:
доска и лента читают всё, как раньше; фильтр — в пульте (reports_tick / send_alerts),
он спрашивает уровень у tide: ``tide notify --source <проект>/<нить>/<сессия> --level-only``.
"""

from __future__ import annotations

import json

import pytest

from tide import cli, fields, notify
from tide.arc import stream

from tests.test_handoff_autospawn import world  # noqa: F401  fixture


@pytest.fixture
def tree(world):
    proj = world["proj"]
    s1 = stream.new_session(proj, "seam", "s1", goal="g")
    thread = s1.parents[1]
    return {"thread": thread, "s1": s1, **world}


def test_default_is_all(tree):
    assert notify.own_level(tree["thread"]) is None
    assert notify.effective(tree["s1"]) == ("all", None)


def test_field_lives_in_thread_passport(tree):
    notify.set_level(tree["thread"], "questions")
    pp = stream.passport_path(tree["thread"])
    assert pp.name.endswith("-goal.md")
    assert fields.read_field(pp, "notify") == "questions"


def test_child_inherits_and_cannot_be_louder(tree):
    notify.set_level(tree["thread"], "questions")
    assert notify.effective(tree["s1"]) == ("questions", tree["thread"])
    notify.set_level(tree["s1"], "all")                 # громче родителя нельзя
    assert notify.effective(tree["s1"])[0] == "questions"
    notify.set_level(tree["s1"], "quiet")               # тише — можно
    assert notify.effective(tree["s1"]) == ("quiet", tree["s1"])
    notify.set_level(tree["thread"], "quiet")
    notify.set_level(tree["s1"], "questions")
    assert notify.effective(tree["s1"]) == ("quiet", tree["thread"])


def test_all_clears_own_field(tree):
    notify.set_level(tree["thread"], "quiet")
    notify.set_level(tree["thread"], "all")
    assert notify.own_level(tree["thread"]) is None
    assert fields.read_field(stream.passport_path(tree["thread"]), "notify") is None


def test_bad_level_refused(tree):
    with pytest.raises(notify.NotifyError):
        notify.set_level(tree["thread"], "loud")


def test_garbage_in_passport_counts_as_unset(tree):
    fields.set_field(stream.passport_path(tree["thread"]), "notify", "громко")
    assert notify.own_level(tree["thread"]) is None
    assert notify.effective(tree["s1"])[0] == "all"


def test_cli_set_and_show(tree, capsys):
    assert cli.main(["notify", "seam", "questions"]) == 0
    capsys.readouterr()
    assert cli.main(["notify", "seam/s1"]) == 0
    out = capsys.readouterr().out
    assert "questions" in out and "seam" in out


def test_cli_json_for_board(tree, capsys):
    assert cli.main(["notify", "--dir", str(tree["thread"]), "quiet"]) == 0
    capsys.readouterr()
    assert cli.main(["notify", "--dir", str(tree["s1"]), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["own"] is None
    assert got["effective"] == "quiet"
    assert got["from"] == str(tree["thread"])
    assert got["levels"] == ["all", "questions", "quiet"]
    assert got["dir"] == str(tree["s1"])


def test_cli_bad_level_exit_2(tree, capsys):
    assert cli.main(["notify", "seam", "loud"]) == 2
    assert "all|questions|quiet" in capsys.readouterr().err


def test_source_resolves_from_anywhere(tree, monkeypatch, capsys, tmp_path):
    notify.set_level(tree["thread"], "questions")
    monkeypatch.chdir(tmp_path)                         # пульт зовёт tide не из проекта
    assert notify.resolve_source("proj/seam/s1") == tree["s1"]
    assert notify.resolve_source("proj/01-@seam/01-s1") == tree["s1"]
    assert notify.resolve_source("proj/seam") == tree["thread"]
    assert notify.resolve_source("nope/seam") is None
    assert notify.level_for_source("proj/seam/s1") == "questions"
    assert notify.level_for_source("nope/x/y") == "all"         # не нашли — как раньше
    assert notify.level_for_source("") == "all"
    assert cli.main(["notify", "--source", "proj/seam/s1", "--level-only"]) == 0
    assert capsys.readouterr().out.strip() == "questions"


def test_source_with_unknown_session_falls_back_to_thread(tree, monkeypatch, tmp_path):
    notify.set_level(tree["thread"], "quiet")
    monkeypatch.chdir(tmp_path)
    assert notify.level_for_source("proj/seam/no-such-session") == "quiet"


def test_nested_arcs_inherit(tree):
    """Вложенная арка (как 01-@ao-control/arcs/02-tg-pult) — тоже ребёнок."""
    inner = tree["s1"] / "arcs" / "01-inner"
    inner.mkdir(parents=True)
    (inner / "arc.md").write_text("slug: inner\n\n# inner\n", encoding="utf-8")
    notify.set_level(tree["s1"], "questions")
    assert notify.effective(inner) == ("questions", tree["s1"])

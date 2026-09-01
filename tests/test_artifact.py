"""tide.arc.artifact — «артефакты»: вещи, которые агент кладёт человеку на стол.

The machine under test: new → taken, the flag decides the kind, every verb
journals, taken requires the human's word (шаг 4 работы 17, tide-stack).
"""

from __future__ import annotations

import pytest

from tide import cli
from tide.arc import artifact


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    return tmp_project


def _text(root, key="01"):
    return (artifact._find(root, key) / "artifact.md").read_text(encoding="utf-8")


# --- add ---------------------------------------------------------------------

def test_add_text_creates_passport_on_the_desk(in_project):
    rc = cli.main(["artifact", "add", "Соседу про релиз — отправить",
                   "--text", "Сосед, выкатили: смотри прод"])
    assert rc == 0
    d = artifact.artifacts_dir(in_project) / "01-sosedu-pro-reliz-otpravit"
    assert d.is_dir()
    text = (d / "artifact.md").read_text(encoding="utf-8")
    assert "# Соседу про релиз — отправить" in text
    assert "kind: message" in text
    assert "status: new" in text
    assert "## содержимое" in text
    assert "Сосед, выкатили: смотри прод" in text
    assert "подан агентом" in text


def test_add_cmd_and_file_take_their_kind_from_the_flag(in_project):
    cli.main(["artifact", "add", "прогнать миграцию", "--cmd", "tide doctor"])
    cli.main(["artifact", "add", "смотреть план", "--file", "/tmp/plan.md"])
    first, second = _text(in_project, "01"), _text(in_project, "02")
    assert "kind: command" in first
    assert artifact.content_of(first) == "tide doctor"
    assert "kind: file" in second
    assert artifact.content_of(second) == "/tmp/plan.md"  # путь как есть


def test_add_says_when_the_file_is_not_there(in_project, capsys):
    cli.main(["artifact", "add", "смотреть план", "--file", "/nope/plan.md"])
    assert "файла по этому пути нет" in capsys.readouterr().out


def test_add_requires_exactly_one_source(in_project, capsys):
    assert cli.main(["artifact", "add", "что-то"]) == 1
    assert "чем подаёшь" in capsys.readouterr().err
    assert cli.main(["artifact", "add", "что-то", "--text", "a", "--cmd", "b"]) == 1
    err = capsys.readouterr().err
    assert "источник ровно один" in err
    assert "--text, --cmd" in err


def test_add_refuses_empty_caption_and_empty_content(in_project, capsys):
    assert cli.main(["artifact", "add", "  ", "--text", "есть что подать"]) == 1
    assert "пустая подпись" in capsys.readouterr().err
    assert cli.main(["artifact", "add", "подпись есть", "--text", "  "]) == 1
    assert "пустое содержимое" in capsys.readouterr().err


def test_add_unfolds_newlines_in_text_but_not_in_cmd(in_project):
    cli.main(["artifact", "add", "письмо", "--text", "первая\\nвторая"])
    cli.main(["artifact", "add", "команда", "--cmd", "printf 'a\\nb'"])
    assert artifact.content_of(_text(in_project, "01")) == "первая\nвторая"
    assert artifact.content_of(_text(in_project, "02")) == "printf 'a\\nb'"


def test_add_numbers_its_own_sequence(in_project):
    cli.main(["work", "add", "работа раз"])
    cli.main(["work", "add", "работа два"])
    cli.main(["artifact", "add", "первая вещь", "--text", "x"])
    assert (artifact.artifacts_dir(in_project) / "01-pervaya-veshch").is_dir()


def test_add_stamps_caller_thread_into_from_arc(in_project, monkeypatch):
    # a session arc pinned to our sid → from-arc names its нить, no flag needed
    sid = "artifact-sid-abc"
    sess = artifact.paths.arcs_dir(in_project) / "09-@build" / "arcs" / "01-do"
    sess.mkdir(parents=True)
    (sess / "arc.md").write_text(
        "# do\n\ntitle: do\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    text = _text(in_project)
    assert "from-arc: 09-@build" in text
    assert "подан агентом (09-@build)" in text


def test_add_without_a_session_leaves_from_arc_empty(in_project):
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    text = _text(in_project)
    assert "from-arc: \n" in text
    assert "— подан агентом\n" in text  # без пустых скобок


def test_add_resolves_work_number_and_refuses_a_dangling_one(in_project, capsys):
    cli.main(["work", "add", "стол issues"])
    assert cli.main(["artifact", "add", "вещь", "--text", "x", "--work", "01"]) == 0
    assert "work: 01" in _text(in_project)
    assert cli.main(["artifact", "add", "вещь", "--text", "x", "--work", "77"]) == 1
    assert "не нашёл работу" in capsys.readouterr().err


def test_add_warns_on_a_long_caption(in_project, capsys):
    cli.main(["artifact", "add", "к" * 90, "--text", "x"])
    assert "подпись длинная" in capsys.readouterr().out


# --- taken / reopen -----------------------------------------------------------

def test_taken_requires_the_human_word(in_project, capsys):
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    assert cli.main(["artifact", "taken", "01", "--word", "  "]) == 1
    assert "забирает человек" in capsys.readouterr().err
    assert "status: new" in _text(in_project)


def test_taken_moves_status_and_journals_the_word(in_project, capsys):
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    capsys.readouterr()
    rc = cli.main(["artifact", "taken", "01", "--word", "забрал, отправил соседу"])
    assert rc == 0
    assert "забран" in capsys.readouterr().out
    text = _text(in_project)
    assert "status: taken" in text
    assert "забран словом: «забрал, отправил соседу»" in text


def test_taken_twice_is_refused(in_project, capsys):
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    cli.main(["artifact", "taken", "01", "--word", "ок"])
    assert cli.main(["artifact", "taken", "01", "--word", "ок"]) == 1
    assert "уже забран" in capsys.readouterr().err


def test_reopen_puts_it_back_and_refuses_a_live_one(in_project, capsys):
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    assert cli.main(["artifact", "reopen", "01"]) == 1
    assert "и так на столе" in capsys.readouterr().err
    cli.main(["artifact", "taken", "01", "--word", "ок"])
    assert cli.main(["artifact", "reopen", "01", "--word", "передумал"]) == 0
    text = _text(in_project)
    assert "status: new" in text
    assert "возвращён на стол по слову: «передумал»" in text


# --- list / show --------------------------------------------------------------

def test_list_shows_live_newest_first_and_counts_taken(in_project, capsys):
    cli.main(["artifact", "add", "первая вещь", "--text", "a"])
    cli.main(["artifact", "add", "вторая вещь", "--cmd", "tide doctor"])
    cli.main(["artifact", "add", "третья вещь", "--text", "c"])
    cli.main(["artifact", "taken", "01", "--word", "ок"])
    capsys.readouterr()
    assert cli.main(["artifact", "list"]) == 0
    out = capsys.readouterr().out
    assert out.index("03-tretya-veshch") < out.index("02-vtoraya-veshch")
    assert "command" in out
    assert "01-pervaya-veshch" not in out  # забранное со стола ушло
    assert "забрано: 1" in out


def test_list_on_an_empty_desk(in_project, capsys):
    assert cli.main(["artifact", "list"]) == 0
    assert "артефактов нет" in capsys.readouterr().out
    cli.main(["artifact", "add", "вещь", "--text", "x"])
    cli.main(["artifact", "taken", "01", "--word", "ок"])
    capsys.readouterr()
    cli.main(["artifact", "list"])
    out = capsys.readouterr().out
    assert "на столе пусто" in out
    assert "забрано: 1" in out


def test_show_prints_the_file_as_it_is(in_project, capsys):
    cli.main(["artifact", "add", "вещь", "--text", "первая\\nвторая"])
    capsys.readouterr()
    assert cli.main(["artifact", "show", "01"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == _text(in_project).strip()
    assert "первая\nвторая" in out


def test_show_and_taken_refuse_an_unknown_key(in_project, capsys):
    assert cli.main(["artifact", "show", "77"]) == 1
    assert "не нашёл артефакт" in capsys.readouterr().err


# --- вопрос агента (шаг 6 работы 25) ------------------------------------------

def test_add_ask_puts_a_question_on_the_desk(in_project, capsys):
    rc = cli.main(["artifact", "add", "Куда катим стейдж — k8s или compose?",
                   "--ask", "Коллега даёт k8s.\\nЖду слова, чем собирать."])
    assert rc == 0
    text = _text(in_project, "01")
    assert "kind: question" in text
    assert "status: new" in text
    # проза, как у сообщения: перенос строки разворачивается
    assert artifact.content_of(text) == "Коллега даёт k8s.\nЖду слова, чем собирать."
    out = capsys.readouterr().out
    # подсказка зовёт ОТВЕТИТЬ СЛОВОМ, а не жать кнопку (решение 06)
    assert "ответит человек словом в сессию" in out
    assert "заберёт человек" not in out


def test_ask_is_a_source_like_the_others(in_project, capsys):
    assert cli.main(["artifact", "add", "что-то"]) == 1
    assert "--ask «вопрос»" in capsys.readouterr().err
    assert cli.main(["artifact", "add", "х", "--ask", "a", "--text", "b"]) == 1
    assert "источник ровно один" in capsys.readouterr().err


def test_question_lives_the_same_two_states(in_project):
    cli.main(["artifact", "add", "спрошу", "--ask", "так или так?"])
    assert cli.main(["artifact", "taken", "01", "--word", "давай так"]) == 0
    text = _text(in_project, "01")
    assert "status: taken" in text
    assert "давай так" in text

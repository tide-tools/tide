"""tide.arc.work — «работы»: deterministic agent gestures over works/*/work.md.

The machine under test: open → taken → review → done, every verb journals,
done requires the human's word (cand 125-work-cli-verbs, model work-cycle.md).
"""

from __future__ import annotations

import pytest

from tide import cli
from tide.arc import work


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    return tmp_project


def _text(root, key="01"):
    return (work._find(root, key) / "work.md").read_text(encoding="utf-8")


# --- add ---------------------------------------------------------------------

def test_add_creates_passport_with_checklist(in_project):
    rc = cli.main(["work", "add", "вылить рефералку",
                   "--deadline", "2026-07-16", "--for", "mite"])
    assert rc == 0
    d = work.works_dir(in_project) / "01-vylit-referalku"
    assert d.is_dir()
    text = (d / "work.md").read_text(encoding="utf-8")
    assert "# вылить рефералку" in text
    assert "status: open" in text
    assert "project: mite" in text
    assert "deadline: 2026-07-16" in text
    assert "- [ ] вылить рефералку" in text


def test_add_rejects_bad_deadline(in_project, capsys):
    rc = cli.main(["work", "add", "x", "--deadline", "завтра"])
    assert rc == 1
    assert "кривой дедлайн" in capsys.readouterr().err


# --- take --------------------------------------------------------------------

def test_take_moves_open_to_taken_and_journals(in_project):
    cli.main(["work", "add", "вылить выплаты"])
    rc = cli.main(["work", "take", "01", "--by", "mite-agent",
                   "--word", "возьми выплаты"])
    assert rc == 0
    text = _text(in_project)
    assert "status: taken" in text
    assert "taken-by: mite-agent" in text
    assert "taken-at: " in text
    assert "## журнал" in text
    assert "взята в работу (mite-agent) по слову: «возьми выплаты»" in text


def test_take_refuses_second_take_and_done(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "take", "01"]) == 1
    assert "уже взята" in capsys.readouterr().err
    cli.main(["work", "close", "01", "--word", "закрывай"])
    assert cli.main(["work", "take", "01"]) == 1
    assert "сначала tide work reopen" in capsys.readouterr().err


# --- check / uncheck ---------------------------------------------------------

def test_check_requires_proof_and_take(in_project, capsys):
    cli.main(["work", "add", "x"])
    # argparse enforces --proof presence; empty proof fails in logic
    assert cli.main(["work", "check", "01", "1", "--proof", "  "]) == 1
    assert "без пруфа" in capsys.readouterr().err
    assert cli.main(["work", "check", "01", "1", "--proof", "done"]) == 1
    assert "сначала tide work take" in capsys.readouterr().err


def test_check_marks_item_journals_proof_and_autoreviews(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    rc = cli.main(["work", "check", "01", "1", "--proof", "коммит abc123"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "→ review" in out  # the last check announces the auto-move
    text = _text(in_project)
    assert "- [x] x" in text
    assert "коммит abc123" in text
    assert "status: review" in text
    assert "все пункты чекнуты → review" in text


def test_check_no_review_while_items_remain(in_project):
    cli.main(["work", "add", "x"])
    d = work.works_dir(in_project) / "01-x" / "work.md"
    d.write_text(d.read_text(encoding="utf-8") + "- [ ] второй\n",
                 encoding="utf-8")
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: taken" in _text(in_project)


def test_check_double_and_missing_index_fail(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "check", "01", "1", "--proof", "p"]) == 1
    assert "уже чекнут" in capsys.readouterr().err
    assert cli.main(["work", "check", "01", "9", "--proof", "p"]) == 1
    assert "нет пункта 9" in capsys.readouterr().err


def test_uncheck_falls_back_from_review_to_taken(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    rc = cli.main(["work", "uncheck", "01", "1", "--reason", "не вылилось"])
    assert rc == 0
    text = _text(in_project)
    assert "- [ ] x" in text
    assert "status: taken" in text
    assert "чеклист снова неполон → taken" in text
    assert "не вылилось" in text


# --- close / reopen ----------------------------------------------------------

def test_close_requires_word_and_journals_it(in_project, capsys):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "close", "01", "--word", " "]) == 1
    assert "done ставит человек" in capsys.readouterr().err
    rc = cli.main(["work", "close", "01", "--word", "закрывай"])
    assert rc == 0
    text = _text(in_project)
    assert "status: done" in text
    assert "закрыта по слову человека: «закрывай»" in text


def test_reopen_only_from_done(in_project, capsys):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "reopen", "01"]) == 1
    assert "и так открыта" in capsys.readouterr().err
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "reopen", "01"]) == 0
    assert "status: open" in _text(in_project)


# --- find / list / show ------------------------------------------------------

def test_find_by_nn_slug_and_ambiguity(in_project, capsys):
    cli.main(["work", "add", "alpha task"])
    cli.main(["work", "add", "beta task"])
    assert work._find(in_project, "alpha-task").name == "01-alpha-task"
    assert work._find(in_project, "02").name == "02-beta-task"
    with pytest.raises(work.WorkError):
        work._find(in_project, "nope")


def test_list_orders_live_by_deadline_closed_last(in_project, capsys):
    cli.main(["work", "add", "late", "--deadline", "2026-08-01"])
    cli.main(["work", "add", "soon", "--deadline", "2026-07-01"])
    cli.main(["work", "add", "gone"])
    cli.main(["work", "close", "03", "--word", "ок"])
    capsys.readouterr()  # drop the add/close prints — we assert on list only
    cli.main(["work", "list"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("02-soon")
    assert out[1].startswith("01-late")
    assert out[2].startswith("03-gone")
    assert "done" in out[2]


def test_show_prints_raw_passport(in_project, capsys):
    cli.main(["work", "add", "x"])
    rc = cli.main(["work", "show", "01"])
    assert rc == 0
    assert "kind: work" in capsys.readouterr().out


# --- cross-project ------------------------------------------------------------

def test_project_flag_targets_rostered_neighbour(tmp_project, tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton
    other = tmp_path / "neighbour"
    other.mkdir()
    build_tide_skeleton(other, name="neighbour")
    home = tmp_project
    (home / "roster.md").write_text(
        "# tide roster\nneighbour | {0}\n".format(other), encoding="utf-8")
    monkeypatch.chdir(home)
    rc = cli.main(["work", "add", "чужая работа", "--project", "neighbour"])
    assert rc == 0
    assert (work.works_dir(other) / "01-chuzhaya-rabota" / "work.md").is_file()

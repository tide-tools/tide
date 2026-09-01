"""tide.arc.work — «работы»: deterministic agent gestures over works/*/work.md.

The machine under test: open → taken → review → done, every verb journals,
done requires the human's word (cand 125-work-cli-verbs, model work-cycle.md).
"""

from __future__ import annotations

import pytest

from tide import cli, paths
from tide.arc import work


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project)
    return tmp_project


def _text(root, key="01"):
    return (work._find(root, key) / "work.md").read_text(encoding="utf-8")


def _seed(item="x", key="01"):
    """Первый пункт чеклиста руками — то, что раньше клал сам ``add``.

    Имя работы больше не становится пунктом (кандидат 183), а нумерация в этих
    тестах считается от первого пункта, как её видит человек на доске.
    """
    cli.main(["work", "checklist", key, item])


# --- add ---------------------------------------------------------------------

def test_add_creates_passport_with_empty_checklist(in_project):
    """Кандидат 183: имя работы — заголовок, а не первый её шаг.

    Пункт-двойник имени случался трижды (работы 23, 41, 44): никто его не делал,
    а счётчик врал (0/6 вместо 0/5), и снять его было нечем.
    """
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
    assert "## чеклист" in text
    assert "- [ ] вылить рефералку" not in text
    assert work.all_items(text) == []


def test_add_rejects_bad_deadline(in_project, capsys):
    rc = cli.main(["work", "add", "x", "--deadline", "завтра"])
    assert rc == 1
    assert "кривой дедлайн" in capsys.readouterr().err


# --- add --cand (кандидат рождает работу) -------------------------------------

def _shelf(root):
    return paths.candidates_dir(root)


def test_add_from_candidate_carries_the_text_and_clears_the_shelf(in_project):
    """Кандидат 174: один верб вместо двух жестов — иначе полка дублирует стол."""
    cli.main(["candidate", "add", "живой пульт", "механика движка: верб рождения"])
    assert (_shelf(in_project) / "01-zhivoy-pult.md").is_file()
    rc = cli.main(["work", "add", "Пульт оживает", "--cand", "01"])
    assert rc == 0
    text = _text(in_project)
    # имя — из аргумента, слаг-простыня кандидата тайтлом не становится
    assert "# Пульт оживает" in text
    assert "status: open" in text          # цикл согласования не обойдён
    assert "## план" in text
    assert "механика движка: верб рождения" in text
    assert "рождена из кандидата 01-zhivoy-pult" in text
    # с полки ушёл, но не с диска
    assert not (_shelf(in_project) / "01-zhivoy-pult.md").exists()
    assert (_shelf(in_project) / "__dropped__" / "01-zhivoy-pult.md").is_file()


def test_add_from_candidate_takes_the_key_by_number_slug_or_both(in_project):
    for key in ("01", "01-odin", "odin"):
        cli.main(["candidate", "add", "один", "тело идеи"])
        assert cli.main(["work", "add", "Работа", "--cand", key]) == 0
        assert not list(_shelf(in_project).glob("*.md"))


def test_add_from_candidate_shouts_when_there_is_no_such_candidate(in_project, capsys):
    rc = cli.main(["work", "add", "Работа", "--cand", "77"])
    assert rc == 1
    assert "не нашёл кандидата" in capsys.readouterr().err
    assert not work.works_dir(in_project).exists()


def test_add_from_candidate_keeps_the_shelf_when_the_work_is_refused(in_project):
    """Кривой дедлайн валит жест ЦЕЛИКОМ: кандидат не должен уехать в никуда."""
    cli.main(["candidate", "add", "один", "тело идеи"])
    assert cli.main(["work", "add", "Работа", "--cand", "01",
                     "--deadline", "завтра"]) == 1
    assert (_shelf(in_project) / "01-odin.md").is_file()


def test_plain_add_still_touches_no_candidate(in_project):
    cli.main(["candidate", "add", "один", "тело идеи"])
    assert cli.main(["work", "add", "Работа"]) == 0
    assert (_shelf(in_project) / "01-odin.md").is_file()
    assert "## план" not in _text(in_project)


def test_candidate_gist_drops_the_header_not_the_prose(in_project):
    """Первая строка идеи часто выглядит ключом («механика движка: …»)."""
    text = ("# 07-ideya\n\nfrom: -\ndropped: 2026-08-01 14:25\n\n"
            "механика движка: верб рождения\nвторая строка\n")
    assert work._candidate_gist(text) == (
        "механика движка: верб рождения\nвторая строка")


# --- checklist ----------------------------------------------------------------

def test_checklist_replaces_raw_item_and_journals(in_project):
    cli.main(["work", "add", "вылить выплаты"])
    rc = cli.main(["work", "checklist", "01", "поднять прод", "прогнать смоук"])
    assert rc == 0
    text = _text(in_project)
    assert "- [ ] поднять прод" in text
    assert "- [ ] прогнать смоук" in text
    assert "- [ ] вылить выплаты" not in text
    assert "чеклист согласован: 2 пункт(ов)" in text


def test_checklist_refuses_over_checked_progress_without_force(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "checklist", "01", "новый"]) == 1
    assert "сотрёт прогресс" in capsys.readouterr().err
    assert cli.main(["work", "checklist", "01", "новый", "--force"]) == 0
    assert "- [ ] новый" in _text(in_project)


# --- plan (## план) -----------------------------------------------------------

def test_plan_lands_between_description_and_checklist(in_project):
    cli.main(["work", "add", "вылить выплаты"])
    rc = cli.main(["work", "plan", "01", "снять слепок\\nпрогнать смоук"])
    assert rc == 0
    text = _text(in_project)
    assert "## план" in text
    assert "снять слепок\nпрогнать смоук" in text  # \n стал переносом
    assert text.index("## план") < text.index("## чеклист")
    assert text.index("created:") < text.index("## план")
    assert "план предложен агентом" in text


def test_plan_replaces_body_and_moves_no_status(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "plan", "01", "первый заход"])
    assert cli.main(["work", "plan", "01", "второй заход"]) == 0
    text = _text(in_project)
    assert "второй заход" in text
    assert "первый заход" not in text
    assert text.count("## план") == 1
    assert "status: open" in text  # план — слово агента, не жест статуса
    assert "план обновлён агентом" in text


def test_plan_refuses_empty_and_closed(in_project, capsys):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "plan", "01", "  "]) == 1
    assert "пустой план" in capsys.readouterr().err
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "plan", "01", "поздно"]) == 1
    assert "сначала tide work reopen" in capsys.readouterr().err


# --- propose (- [?]) ----------------------------------------------------------

def test_propose_appends_question_items_with_real_numbers(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])  # даёт пункт 1
    _seed("вылить выплаты")
    capsys.readouterr()
    rc = cli.main(["work", "propose", "01", "поднять прод", "прогнать смоук"])
    assert rc == 0
    assert "предложены шаги 2–3" in capsys.readouterr().out
    text = _text(in_project)
    assert "- [ ] вылить выплаты" in text
    assert "- [?] поднять прод" in text
    assert "- [?] прогнать смоук" in text
    assert "предложены шаги 2–3 (ждут «да» человека)" in text
    assert "status: open" in text


def test_propose_hint_puts_the_verb_first_no_bare_button_promise(in_project, capsys):
    """Панель релиза, находка 2: коробочная доска read-only — кнопок нет.

    Подсказка после propose обязана вести к команде (tide work agree --word);
    кнопка упоминается только как вариант живой доски, не как первый путь.
    """
    cli.main(["work", "add", "x"])
    _seed()
    capsys.readouterr()
    cli.main(["work", "propose", "01", "шаг"])
    out = capsys.readouterr().out
    assert "tide work agree --word" in out
    assert "кнопка на доске" not in out  # обещание кнопки, которой в коробке нет
    assert "живой доске" in out


def test_review_hint_puts_close_word_first_no_bare_button_promise(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    capsys.readouterr()
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    out = capsys.readouterr().out
    assert "→ review" in out
    assert "tide work close --word" in out
    assert "кнопка на доске" not in out
    assert "живой доске" in out


def test_propose_single_item_speaks_singular(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    assert cli.main(["work", "propose", "01", "добить хвост"]) == 0
    assert "предложен шаг 2 (ждёт «да» человека)" in _text(in_project)


def test_standing_offer_holds_review_back(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "может ещё это"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    text = _text(in_project)
    # чеклист закрыт, а разговор нет: пока висит «- [?]» — работа не на приёмку
    assert "status: taken" in text
    assert "- [?] может ещё это" in text
    assert "→ review" not in text
    # предложение всё так же не считается прогрессом — в чеклисте один пункт
    assert work.items(text) == [(True, "x")]


def test_check_refuses_a_proposed_item_and_keeps_the_file(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "ещё не согласовано"])
    cli.main(["work", "take", "01"])
    before = _text(in_project)
    assert cli.main(["work", "check", "01", "2", "--proof", "сделал"]) == 1
    assert "пункт 2 — ещё предложение" in capsys.readouterr().err
    assert _text(in_project) == before  # отказ файл не трогает


def test_uncheck_refuses_a_proposed_item(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "ещё не согласовано"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "uncheck", "01", "2"]) == 1
    assert "ждёт «да» человека" in capsys.readouterr().err


def test_checklist_agreement_replaces_proposals(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "propose", "01", "предложение"])
    assert cli.main(["work", "checklist", "01", "согласовано"]) == 0
    text = _text(in_project)
    assert "- [ ] согласовано" in text
    assert "[?]" not in text


# --- описания пунктов (тайтл + подробности отступом) --------------------------

def test_propose_splits_title_and_description(in_project):
    cli.main(["work", "add", "x"])
    rc = cli.main(["work", "propose", "01",
                   "Переучить ритуал передачи\\nсначала draft — потом курация"])
    assert rc == 0
    text = _text(in_project)
    assert "- [?] Переучить ритуал передачи\n  сначала draft — потом курация\n" in text


def test_description_keeps_multiple_lines_under_one_item(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Тайтл\\nпервая\\nвторая", "Второй пункт"])
    text = _text(in_project)
    assert "- [?] Тайтл\n  первая\n  вторая\n- [?] Второй пункт\n" in text
    # описание — не пункт: номера считают только строки «- [»
    assert [t for _, t in work.all_items(text)] == ["x", "Тайтл", "Второй пункт"]


def test_item_blocks_read_description_back(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Тайтл\\nподробности"])
    blocks = work.item_blocks(_text(in_project))
    assert blocks == [(" ", "x", ""), ("?", "Тайтл", "подробности")]


def test_description_does_not_shift_item_numbers(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Первый\\nдетали первого", "Второй"])
    cli.main(["work", "take", "01"])
    capsys.readouterr()
    assert cli.main(["work", "check", "01", "2", "--proof", "коммит abc"]) == 0
    text = _text(in_project)
    assert "- [ ] Первый\n  детали первого\n- [x] Второй\n" in text
    assert "пункт 2 ✓ «Второй»" in text  # в журнал идёт тайтл, не описание


def test_check_keeps_description_and_autoreviews(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Единственный\\nподробности"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "check", "01", "1", "--proof", "p"]) == 0
    text = _text(in_project)
    assert "- [x] Единственный\n  подробности\n" in text
    assert "status: review" in text  # описание не считается недоделанным пунктом


def test_uncheck_by_index_survives_descriptions(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Первый\\nдетали", "Второй\\nещё детали"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "2", "--proof", "p"])
    assert cli.main(["work", "uncheck", "01", "2"]) == 0
    text = _text(in_project)
    assert "- [ ] Второй\n  ещё детали\n" in text


def test_item_without_title_is_refused(in_project, capsys):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "propose", "01", "\\nтолько описание"]) == 1
    assert "пункт без заголовка" in capsys.readouterr().err


def test_show_prints_description_indented_under_title(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "propose", "01", "Тайтл\\nподробности"])
    capsys.readouterr()
    assert cli.main(["work", "show", "01"]) == 0
    assert "- [?] Тайтл\n  подробности" in capsys.readouterr().out


# --- propose --replace --------------------------------------------------------

def test_propose_replace_withdraws_old_offers_with_descriptions(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Старое\\nстарые детали", "Тоже старое"])
    capsys.readouterr()
    rc = cli.main(["work", "propose", "01", "--replace", "Новое\\nновые детали"])
    assert rc == 0
    assert "предложения заменены: шаг 2" in capsys.readouterr().out
    text = _text(in_project)
    assert "Старое" not in text.split("## журнал")[0]
    assert "старые детали" not in text
    assert "Тоже старое" not in text.split("## журнал")[0]
    assert "- [?] Новое\n  новые детали\n" in text
    assert "предложения заменены: шаг 2 (ждёт «да» человека)" in text


def test_propose_replace_keeps_agreed_and_checked_items(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Согласованный", "Второй согласованный"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    cli.main(["work", "propose", "01", "Предложение"])
    assert cli.main(["work", "propose", "01", "--replace", "А теперь так",
                     "И вот так"]) == 0
    text = _text(in_project)
    assert "- [x] Согласованный" in text
    assert "- [ ] Второй согласованный" in text
    assert "- [?] Предложение" not in text
    assert "- [?] А теперь так" in text
    assert "предложения заменены: шаги 3–4" in text
    assert "status: taken" in text  # замена предложений — не жест статуса


def test_propose_without_replace_still_appends(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "propose", "01", "Первое предложение"])
    assert cli.main(["work", "propose", "01", "Второе предложение"]) == 0
    text = _text(in_project)
    assert "- [?] Первое предложение" in text
    assert "- [?] Второе предложение" in text


# --- agree (слово человека вместо кнопки) -------------------------------------

def test_agree_flips_proposal_and_keeps_description(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Поднять прод\\nсначала смоук"])
    capsys.readouterr()
    rc = cli.main(["work", "agree", "01", "2", "--word", "да, давай так"])
    assert rc == 0
    assert "согласовано: шаг 2" in capsys.readouterr().out
    text = _text(in_project)
    assert "- [ ] Поднять прод\n  сначала смоук\n" in text
    assert "[?]" not in text
    assert "пункт 2 подтверждён словом: «да, давай так»" in text
    assert "status: open" in text  # согласование — не жест статуса


def test_agree_refuses_without_word_and_keeps_the_file(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "предложение"])
    before = _text(in_project)
    assert cli.main(["work", "agree", "01", "2", "--word", "  "]) == 1
    assert "согласовывает человек" in capsys.readouterr().err
    assert _text(in_project) == before  # отказ файл не трогает


def test_agree_refuses_agreed_and_checked_items(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Согласованный", "Второй"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "agree", "01", "1", "--word", "ага"]) == 1
    assert "пункт 1 чекнут — это уже не предложение" in capsys.readouterr().err
    assert cli.main(["work", "agree", "01", "2", "--word", "ага"]) == 1
    assert "пункт 2 согласован — это уже не предложение" in capsys.readouterr().err
    assert cli.main(["work", "agree", "01", "9", "--word", "ага"]) == 1
    assert "нет пункта 9" in capsys.readouterr().err


def test_agree_without_numbers_takes_every_offer(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "propose", "01", "Первое\\nдетали", "Второе"])
    assert cli.main(["work", "agree", "01", "--word", "да всё верно"]) == 0
    text = _text(in_project)
    assert "- [ ] Первое\n  детали\n- [ ] Второе\n" in text
    assert "предложения подтверждены словом: «да всё верно»" in text


def test_agree_all_flag_matches_bare_call(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "propose", "01", "Первое", "Второе"])
    capsys.readouterr()
    assert cli.main(["work", "agree", "01", "--all", "--word", "да"]) == 0
    assert "(все предложения)" in capsys.readouterr().out
    text = _text(in_project)
    assert "[?]" not in text
    assert "предложения подтверждены словом: «да»" in text


def test_agree_all_refuses_when_nothing_is_offered(in_project, capsys):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "agree", "01", "--word", "да"]) == 1
    assert "нет предложенных пунктов" in capsys.readouterr().err


def test_agree_takes_part_of_the_offer_and_leaves_the_rest(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Первое", "Второе", "Третье"])
    assert cli.main(["work", "agree", "01", "2", "4", "--word", "эти два"]) == 0
    text = _text(in_project)
    assert "- [ ] Первое\n- [?] Второе\n- [ ] Третье\n" in text
    assert "пункт 2 подтверждён словом: «эти два»" in text
    assert "пункт 4 подтверждён словом: «эти два»" in text


# --- agree --drop (симметричное «нет») ----------------------------------------

def test_agree_drop_removes_item_with_its_description(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Лишнее\\nлишние детали", "Нужное"])
    capsys.readouterr()
    rc = cli.main(["work", "agree", "01", "--drop", "2", "--word", "это не надо"])
    assert rc == 0
    assert "снято: шаг 2" in capsys.readouterr().out
    text = _text(in_project)
    assert "Лишнее" not in text.split("## журнал")[0]
    assert "лишние детали" not in text
    assert "- [?] Нужное" in text
    assert "пункт 2 снят словом: «это не надо»" in text


def test_agree_drop_renumbers_the_rest_for_the_next_gesture(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Первое", "Второе", "Третье"])
    assert cli.main(["work", "agree", "01", "--drop", "2", "--word", "нет"]) == 0
    assert [t for _, t in work.all_items(_text(in_project))] == [
        "x", "Второе", "Третье"]
    # «Второе» съехало на номер 2 — согласовываем по НОВОМУ номеру
    assert cli.main(["work", "agree", "01", "2", "--word", "да"]) == 0
    assert "- [ ] Второе\n- [?] Третье\n" in _text(in_project)


def test_agree_drop_several_keeps_the_numbers_the_human_saw(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Первое", "Второе\\nдетали", "Третье"])
    assert cli.main(["work", "agree", "01", "--drop", "2", "4",
                     "--word", "оба мимо"]) == 0
    text = _text(in_project)
    assert [t for _, t in work.all_items(text)] == ["x", "Второе"]
    assert "пункт 2 снят словом: «оба мимо»" in text
    assert "пункт 4 снят словом: «оба мимо»" in text


def test_agree_drop_refuses_agreed_item_and_mixed_call(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "Предложение"])
    assert cli.main(["work", "agree", "01", "--drop", "1", "--word", "нет"]) == 1
    assert "уже не предложение" in capsys.readouterr().err
    assert cli.main(["work", "agree", "01", "2", "--drop", "2",
                     "--word", "и то и то"]) == 1
    assert "--drop — отдельный жест" in capsys.readouterr().err
    assert "- [?] Предложение" in _text(in_project)


# --- гейт review: висящее предложение держит приёмку ---------------------------

def test_propose_pulls_a_reviewed_work_back_to_taken(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: review" in _text(in_project)
    capsys.readouterr()
    assert cli.main(["work", "propose", "01", "а ещё вот это"]) == 0
    assert "работа вернулась в работу" in capsys.readouterr().out
    text = _text(in_project)
    assert "status: taken" in text
    assert "предложены шаги — работа вернулась в работу (review → taken)" in text


def test_agree_of_last_offer_waits_for_the_proof(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])   # → review
    cli.main(["work", "propose", "01", "Добить хвост"])      # → обратно в taken
    assert cli.main(["work", "agree", "01", "2", "--word", "да, добей"]) == 0
    text = _text(in_project)
    assert "- [ ] Добить хвост" in text
    assert "status: taken" in text  # согласовано ≠ сделано
    capsys.readouterr()
    assert cli.main(["work", "check", "01", "2", "--proof", "коммит abc"]) == 0
    assert "→ review" in capsys.readouterr().out
    assert "status: review" in _text(in_project)


def test_agree_pulls_back_a_work_left_in_review_with_offers(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])   # → review
    f = work._find(in_project, "01") / "work.md"
    # так и выглядела работа 15: старый движок пускал в review с висящим «- [?]»
    f.write_text(_text(in_project).replace("- [x] x", "- [x] x\n- [?] Хвост"),
                 encoding="utf-8")
    assert cli.main(["work", "agree", "01", "2", "--word", "да"]) == 0
    text = _text(in_project)
    assert "status: taken" in text
    assert "чеклист снова неполон → taken" in text


def test_drop_of_last_offer_closes_the_review_gate(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "propose", "01", "Может ещё это"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: taken" in _text(in_project)  # предложение держит гейт
    capsys.readouterr()
    rc = cli.main(["work", "agree", "01", "--drop", "2", "--word", "нет, хватит"])
    assert rc == 0
    assert "→ review" in capsys.readouterr().out
    text = _text(in_project)
    assert "status: review" in text
    assert "все пункты чекнуты → review" in text


def test_drop_of_one_of_two_offers_keeps_the_work_open(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "propose", "01", "Первое", "Второе"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "agree", "01", "--drop", "2", "--word", "нет"]) == 0
    assert "status: taken" in _text(in_project)  # второе предложение ещё висит


# --- fix (накидка человека у приёмки) -----------------------------------------

def test_fix_refuses_without_word_and_keeps_the_file(in_project, capsys):
    cli.main(["work", "add", "x"])
    before = _text(in_project)
    assert cli.main(["work", "fix", "01", "а ещё вот это", "--word", "  "]) == 1
    assert "фикс несёт накидку человека" in capsys.readouterr().err
    assert _text(in_project) == before  # отказ файл не трогает


def test_fix_lands_agreed_in_its_own_section(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])
    _seed("вылить выплаты")
    capsys.readouterr()
    rc = cli.main(["work", "fix", "01", "Добить хвост\\nещё и кабинет",
                   "--word", "тут ещё хвост остался"])
    assert rc == 0
    assert "фикс 2 добавлен" in capsys.readouterr().out
    text = _text(in_project)
    assert "## фиксы" in text
    # слово человека = согласование: пункт сразу «- [ ]», пунктир не нужен
    assert "- [ ] Добить хвост\n  ещё и кабинет\n" in text
    assert "[?]" not in text
    assert "фикс 2 добавлен словом: «тут ещё хвост остался»" in text
    # секция между чеклистом и журналом — номера продолжают чеклист
    assert text.index("## чеклист") < text.index("## фиксы")
    assert text.index("## фиксы") < text.index("## журнал")


def test_fix_numbering_runs_through_both_sections(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Первый", "Второй", "Третий", "Четвёртый"])
    capsys.readouterr()
    assert cli.main(["work", "fix", "01", "Пятым будет фикс",
                     "--word", "допили"]) == 0
    assert "фикс 5 добавлен" in capsys.readouterr().out
    assert [t for _, t in work.all_items(_text(in_project))] == [
        "Первый", "Второй", "Третий", "Четвёртый", "Пятым будет фикс"]
    # чек по сквозному номеру попадает в фикс, а не в чеклист
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "check", "01", "5", "--proof", "коммит abc"]) == 0
    text = _text(in_project)
    assert "- [x] Пятым будет фикс" in text
    assert "пункт 5 ✓ «Пятым будет фикс»" in text
    assert "- [ ] Четвёртый" in text  # соседний номер не съехал


def test_fix_span_speaks_plural(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    capsys.readouterr()
    assert cli.main(["work", "fix", "01", "Раз", "Два", "--word", "и вот это"]) == 0
    assert "фиксы 2–3 добавлены" in capsys.readouterr().out
    assert "фиксы 2–3 добавлены словом: «и вот это»" in _text(in_project)


def test_second_fix_appends_to_the_same_section(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "fix", "01", "Первый фикс", "--word", "раз"])
    assert cli.main(["work", "fix", "01", "Второй фикс", "--word", "два"]) == 0
    text = _text(in_project)
    assert text.count("## фиксы") == 1
    assert "- [ ] Первый фикс\n- [ ] Второй фикс\n" in text
    assert "фикс 3 добавлен словом: «два»" in text


def test_fix_pulls_a_reviewed_work_back_to_taken(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: review" in _text(in_project)
    capsys.readouterr()
    assert cli.main(["work", "fix", "01", "Ещё вот это",
                     "--word", "почти, но добей"]) == 0
    assert "работа вернулась в работу" in capsys.readouterr().out
    text = _text(in_project)
    assert "status: taken" in text
    assert "фикс вернул в работу (review → taken)" in text


def test_review_waits_for_the_fixes_too(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])       # → review
    cli.main(["work", "fix", "01", "Хвост", "--word", "добей"])  # → обратно
    assert "status: taken" in _text(in_project)  # чеклист закрыт, фикс — нет
    capsys.readouterr()
    assert cli.main(["work", "check", "01", "2", "--proof", "коммит abc"]) == 0
    assert "→ review" in capsys.readouterr().out
    assert "status: review" in _text(in_project)


def test_uncheck_of_a_fix_falls_back_from_review(in_project):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "fix", "01", "Хвост", "--word", "добей"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    cli.main(["work", "check", "01", "2", "--proof", "p"])
    assert "status: review" in _text(in_project)
    assert cli.main(["work", "uncheck", "01", "2", "--reason", "не легло"]) == 0
    text = _text(in_project)
    assert "- [ ] Хвост" in text
    assert "status: taken" in text


def test_fix_refuses_a_closed_work(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "fix", "01", "поздно", "--word", "ещё бы"]) == 1
    assert "сначала tide work reopen" in capsys.readouterr().err


def test_checklist_replacement_leaves_the_fixes_alone(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "fix", "01", "Накидка", "--word", "вот это тоже"])
    assert cli.main(["work", "checklist", "01", "Новый шаг"]) == 0
    text = _text(in_project)
    assert "- [ ] Новый шаг" in text
    assert "- [ ] Накидка" in text
    assert text.index("Новый шаг") < text.index("Накидка")


def test_proposal_takes_its_number_before_the_fixes(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "fix", "01", "Накидка", "--word", "ещё вот это"])
    capsys.readouterr()
    # предложение садится в чеклист — номер 2, фикс уезжает на 3
    assert cli.main(["work", "propose", "01", "Предложение"]) == 0
    assert "предложен шаг 2" in capsys.readouterr().out
    assert [t for _, t in work.all_items(_text(in_project))] == [
        "x", "Предложение", "Накидка"]
    assert cli.main(["work", "agree", "01", "2", "--word", "да"]) == 0
    text = _text(in_project)
    assert "- [ ] Предложение\n" in text
    assert "пункт 2 подтверждён словом: «да»" in text


def test_list_counts_both_blocks_as_one_progress(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "fix", "01", "Накидка", "--word", "ещё это"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    capsys.readouterr()
    cli.main(["work", "list"])
    assert "1/2" in capsys.readouterr().out


def test_show_prints_the_fixes_section(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "fix", "01", "Накидка\\nподробности", "--word", "и вот это"])
    capsys.readouterr()
    assert cli.main(["work", "show", "01"]) == 0
    out = capsys.readouterr().out
    assert "## фиксы" in out
    assert out.index("## чеклист") < out.index("## фиксы")
    assert "- [ ] Накидка\n  подробности" in out


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


# --- dispatch (строитель отправлен) -------------------------------------------

def test_dispatch_journals_the_builder_and_moves_no_status(in_project, capsys):
    """Кандидаты 179/180: отправку строителя больше не держит голова."""
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    capsys.readouterr()
    rc = cli.main(["work", "dispatch", "01", "--to", "04-pult"])
    assert rc == 0
    assert "строитель отправлен: 04-pult" in capsys.readouterr().out
    text = _text(in_project)
    assert "строитель отправлен: 04-pult" in text
    assert "status: taken" in text  # событие, а не состояние


def test_dispatch_again_is_another_line_not_an_error(in_project):
    """Воркера переотправляют — каждая отправка свой факт со своим временем."""
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "dispatch", "01", "--to", "первый"]) == 0
    assert cli.main(["work", "dispatch", "01", "--to", "второй"]) == 0
    text = _text(in_project)
    assert "строитель отправлен: первый" in text
    assert "строитель отправлен: второй" in text


def test_dispatch_refuses_a_work_nobody_took(in_project, capsys):
    """Диспатч на open — дырка цикла: плана нет, «да» не сказано."""
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "dispatch", "01", "--to", "воркер"]) == 1
    assert "строителя шлют на ВЗЯТУЮ" in capsys.readouterr().err
    assert "строитель отправлен" not in _text(in_project)


def test_dispatch_refuses_review_and_done(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "п"])  # → review
    assert cli.main(["work", "dispatch", "01", "--to", "воркер"]) == 1
    assert "строителя шлют на ВЗЯТУЮ" in capsys.readouterr().err
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "dispatch", "01", "--to", "воркер"]) == 1
    assert "сначала tide work reopen" in capsys.readouterr().err


def test_dispatch_wants_a_name(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "dispatch", "01", "--to", "  "]) == 1
    assert "кого отправили" in capsys.readouterr().err


# --- responsible thread (нить) ------------------------------------------------

def test_take_records_explicit_thread(in_project):
    cli.main(["work", "add", "x"])
    rc = cli.main(["work", "take", "01", "--thread", "19-@work"])
    assert rc == 0
    text = _text(in_project)
    assert "thread: 19-@work" in text
    assert "нить 19-@work" in text  # journal tail names the owner


def test_thread_verb_sets_and_clears(in_project):
    cli.main(["work", "add", "x"])
    assert cli.main(["work", "thread", "01", "--set", "12-@news"]) == 0
    assert "thread: 12-@news" in _text(in_project)
    assert "ответственная нить → 12-@news" in _text(in_project)
    assert cli.main(["work", "thread", "01", "--clear"]) == 0
    text = _text(in_project)
    assert "thread:" not in text
    assert "нить снята" in text


def test_take_auto_resolves_caller_thread(in_project, monkeypatch):
    # a session arc pinned to our sid → take stamps its нить with no flag
    sid = "auto-sid-abc"
    sess = work.paths.arcs_dir(in_project) / "09-@build" / "arcs" / "01-do"
    sess.mkdir(parents=True)
    (sess / "arc.md").write_text(
        "# do\n\ntitle: do\nclaude-session: {0}\n".format(sid),
        encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    cli.main(["work", "add", "y"])
    assert cli.main(["work", "take", "01"]) == 0
    assert "thread: 09-@build" in _text(in_project)


def test_thread_works_lists_only_the_live_ones_of_that_thread(in_project):
    cli.main(["work", "add", "своя живая"])
    _seed()
    cli.main(["work", "add", "своя закрытая"])
    cli.main(["work", "add", "чужая"])
    cli.main(["work", "thread", "01", "--set", "25-@sloy"])
    cli.main(["work", "thread", "02", "--set", "25-@sloy"])
    cli.main(["work", "thread", "03", "--set", "07-@drugaya"])
    cli.main(["work", "close", "02", "--word", "готово"])

    recs = work.thread_works(in_project, "25-@sloy")
    assert [r["num"] for r in recs] == ["01"]
    assert recs[0]["title"] == "своя живая"
    assert (recs[0]["status"], recs[0]["done"], recs[0]["total"]) == ("open", 0, 1)
    # закрытые видны только когда их просят
    assert [r["num"] for r in work.thread_works(in_project, "25-@sloy",
                                                live_only=False)] == ["01", "02"]


def test_thread_works_matches_a_cross_project_address(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "thread", "01", "--set", "tide-stack/25-@sloy"])
    assert len(work.thread_works(in_project, "25-@sloy")) == 1


def test_thread_works_counts_progress_and_reads_the_cursor(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "раз", "два", "три"])
    cli.main(["work", "thread", "01", "--set", "25-@sloy"])
    cli.main(["work", "take", "01", "--thread", "25-@sloy"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    cli.main(["work", "at", "01", "2"])
    (rec,) = work.thread_works(in_project, "25-@sloy")
    assert (rec["status"], rec["done"], rec["total"], rec["at"]) == ("taken", 1, 3, 2)


def test_thread_works_skips_a_broken_card(in_project):
    cli.main(["work", "add", "живая"])
    cli.main(["work", "thread", "01", "--set", "25-@sloy"])
    broken = work.works_dir(in_project) / "02-bitaya"
    broken.mkdir()
    (broken / "work.md").write_text("# битая\n\nthread: 25-@sloy\n", encoding="utf-8")
    assert [r["num"] for r in work.thread_works(in_project, "25-@sloy")] == ["01"]


# --- нить и шаг: куда работа идёт (кандидаты 182 / работа 44) -----------------

PLAN = """# план нити

## шаги

- [x] 1. первый | что делается | результат: «уже сделано»
- [>] 2. работа знает свой шаг | связь плана и работы | результат: «видно куда»
- [ ] 3. карта стека | разложить всё | результат: «вижу на странице»

## текущий шаг — 2 (работа знает свой шаг)
"""


def _caller_in_thread(root, monkeypatch, thread="09-@build", plan=PLAN):
    """Сессия, сидящая в нити с планом, — из неё и пишет агент."""
    sid = "sid-{0}".format(thread)
    tdir = work.paths.arcs_dir(root) / thread
    sess = tdir / "arcs" / "01-do"
    sess.mkdir(parents=True)
    (sess / "arc.md").write_text(
        "# do\n\ntitle: do\nclaude-session: {0}\n".format(sid), encoding="utf-8")
    if plan is not None:
        (tdir / "plan.md").write_text(plan, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    return tdir


def test_add_stamps_the_callers_thread_and_its_current_step(in_project, monkeypatch,
                                                            capsys):
    """Кандидат 182: работа видна во вкладке нити СРАЗУ, а не после «да»."""
    _caller_in_thread(in_project, monkeypatch)
    assert cli.main(["work", "add", "Работа знает шаг"]) == 0
    assert "шаг 2 «работа знает свой шаг»" in capsys.readouterr().out
    text = _text(in_project)
    assert "thread: 09-@build" in text
    assert "step: 2" in text
    assert text.index("thread:") < text.index("step:")  # шаг читается рядом с нитью
    assert "ответственная нить → 09-@build (сессия-автор)" in text
    assert "status: open" in text  # адрес — не жест статуса


def test_plan_and_propose_stamp_the_thread_before_the_human_says_yes(
        in_project, monkeypatch, capsys):
    cli.main(["work", "add", "без нити"])          # заведена вне сессии-нити
    assert "thread:" not in _text(in_project)
    _caller_in_thread(in_project, monkeypatch)
    assert cli.main(["work", "plan", "01", "как думаю делать"]) == 0
    assert "работа видна во вкладке нити уже сейчас" in capsys.readouterr().out
    assert "thread: 09-@build" in _text(in_project)
    cli.main(["work", "add", "вторая"])
    cli.main(["work", "thread", "02", "--clear"])
    assert cli.main(["work", "propose", "02", "шаг"]) == 0
    assert "thread: 09-@build" in _text(in_project, "02")


def test_auto_thread_never_overwrites_an_explicit_one(in_project, monkeypatch):
    cli.main(["work", "add", "чужая"])
    cli.main(["work", "thread", "01", "--set", "77-@drugaya"])
    _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "plan", "01", "план"])
    cli.main(["work", "propose", "01", "шаг"])
    text = _text(in_project)
    assert "thread: 77-@drugaya" in text
    assert "09-@build" not in text


def test_step_stays_unset_when_the_plan_cannot_say(in_project, monkeypatch):
    """Гадать нельзя: нет плана / нет раздела / никто не помечен [>] — поля нет."""
    _caller_in_thread(in_project, monkeypatch, thread="01-@bez-plana", plan=None)
    cli.main(["work", "add", "раз"])
    assert "step:" not in _text(in_project)

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    _caller_in_thread(in_project, monkeypatch, thread="02-@bez-shagov",
                      plan="# план\n\n## финал\n\nтекст\n")
    cli.main(["work", "add", "два"])
    assert "step:" not in _text(in_project, "02")

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    _caller_in_thread(in_project, monkeypatch, thread="03-@bez-tekushchego",
                      plan="# план\n\n## шаги\n\n- [ ] 1. будущий | x | y\n")
    cli.main(["work", "add", "три"])
    assert "step:" not in _text(in_project, "03")


def test_take_refreshes_the_step_from_the_plan(in_project, monkeypatch):
    tdir = _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "add", "работа"])
    assert "step: 2" in _text(in_project)
    # пока работу согласовывали, план шагнул дальше
    (tdir / "plan.md").write_text(PLAN.replace("- [>] 2.", "- [x] 2.")
                                  .replace("- [ ] 3.", "- [>] 3."), encoding="utf-8")
    assert cli.main(["work", "take", "01"]) == 0
    assert "step: 3" in _text(in_project)


def test_step_follows_the_thread_and_leaves_with_it(in_project, monkeypatch):
    _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "add", "работа"])
    assert cli.main(["work", "thread", "01", "--set", "77-@bez-plana"]) == 0
    text = _text(in_project)
    assert "step:" not in text  # у новой нити плана нет — старый адрес снят
    assert "шаг снят (в плане нити его нет)" in text
    assert cli.main(["work", "thread", "01", "--set", "09-@build"]) == 0
    assert "step: 2" in _text(in_project)
    assert cli.main(["work", "thread", "01", "--clear"]) == 0
    assert "step:" not in _text(in_project)


def test_step_verb_sets_names_and_clears(in_project, monkeypatch, capsys):
    _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "add", "работа"])
    capsys.readouterr()
    assert cli.main(["work", "step", "01", "--set", "3"]) == 0
    assert "шаг плана 3 «карта стека»" in capsys.readouterr().out
    text = _text(in_project)
    assert "step: 3" in text
    assert "шаг плана → 3 «карта стека» (рукой)" in text
    assert cli.main(["work", "step", "01", "--clear"]) == 0
    assert "step:" not in _text(in_project)


def test_step_refuses_a_work_without_a_thread(in_project, capsys):
    cli.main(["work", "add", "ничья"])
    assert cli.main(["work", "step", "01", "--set", "1"]) == 1
    assert "шаг это адрес В ПЛАНЕ НИТИ" in capsys.readouterr().err
    assert cli.main(["work", "step", "01"]) == 1
    assert "номер шага" in capsys.readouterr().err


def test_show_says_where_the_work_is_heading(in_project, monkeypatch, capsys):
    _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "add", "работа"])
    capsys.readouterr()
    assert cli.main(["work", "show", "01"]) == 0
    out = capsys.readouterr().out
    assert "step: 2" in out
    assert "куда идёт — нить 09-@build, шаг 2 «работа знает свой шаг»" in out


def test_thread_works_carries_the_step(in_project, monkeypatch):
    _caller_in_thread(in_project, monkeypatch)
    cli.main(["work", "add", "работа"])
    (rec,) = work.thread_works(in_project, "09-@build")
    assert rec["step"] == 2


def test_step_reads_a_cross_project_address(tmp_project, tmp_path, monkeypatch):
    """Нить соседнего проекта ищется по ростеру — адрес на карточке живой."""
    from tests.conftest import build_tide_skeleton
    other = tmp_path / "neighbour"
    other.mkdir()
    build_tide_skeleton(other, name="neighbour")
    (work.paths.arcs_dir(other) / "26-@release").mkdir(parents=True)
    (work.paths.arcs_dir(other) / "26-@release" / "plan.md").write_text(
        PLAN, encoding="utf-8")
    (tmp_project / "roster.md").write_text(
        "# tide roster\nneighbour | {0}\n".format(other), encoding="utf-8")
    monkeypatch.chdir(tmp_project)
    cli.main(["work", "add", "работа"])
    assert cli.main(["work", "thread", "01", "--set", "neighbour/26-@release"]) == 0
    assert "step: 2" in _text(tmp_project)


# --- drop (снять согласованный пункт словом человека) -------------------------

def test_drop_removes_the_item_with_its_description_and_journals(in_project, capsys):
    """Кандидат 183: снять обычный пункт было НЕЧЕМ — только словом человека."""
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Лишний\\nлишние детали", "Нужный"])
    capsys.readouterr()
    rc = cli.main(["work", "drop", "01", "1", "--word", "этот пункт не нужен"])
    assert rc == 0
    assert "пункт 1 «Лишний» снят" in capsys.readouterr().out
    text = _text(in_project)
    assert "Лишний" not in text.split("## журнал")[0]
    assert "лишние детали" not in text
    assert [t for _, t in work.all_items(text)] == ["Нужный"]
    assert "пункт 1 «Лишний» снят словом: «этот пункт не нужен»" in text


def test_drop_requires_the_human_word_and_keeps_the_file(in_project, capsys):
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Пункт"])
    before = _text(in_project)
    assert cli.main(["work", "drop", "01", "1", "--word", "  "]) == 1
    assert "снять согласованный пункт может человек" in capsys.readouterr().err
    assert _text(in_project) == before


def test_drop_sends_a_proposal_and_a_checked_item_to_their_own_gestures(
        in_project, capsys):
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Сделанный"])
    cli.main(["work", "propose", "01", "Предложенный"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "drop", "01", "2", "--word", "нет"]) == 1
    assert "agree <key> --drop 2" in capsys.readouterr().err
    assert cli.main(["work", "drop", "01", "1", "--word", "нет"]) == 1
    assert "сначала tide work uncheck 1" in capsys.readouterr().err
    assert cli.main(["work", "drop", "01", "9", "--word", "нет"]) == 1
    assert "нет пункта 9" in capsys.readouterr().err


def test_drop_moves_the_cursor_and_the_acceptances_with_the_numbers(in_project):
    """Номер пункта — адрес: живые ссылки на него едут следом, история — нет."""
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Первый", "Второй", "Третий"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "3", "--proof", "p"])
    _hand_accepts(in_project, 3)
    cli.main(["work", "at", "01", "2"])
    assert cli.main(["work", "drop", "01", "1", "--word", "первый лишний"]) == 0
    text = _text(in_project)
    assert [t for _, t in work.all_items(text)] == ["Второй", "Третий"]
    assert "\nat: 1" in text                       # курсор переехал за «Вторым»
    assert "пункт 2 принят рукой" in text          # приёмка «Третьего» — тоже
    assert work.accepted_items(text) == [2]
    # история жеста цела: «пункт 3 ✓» так и остался рассказом со своим текстом
    assert "пункт 3 ✓ «Третий»" in text


def test_drop_of_the_cursors_own_item_takes_the_cursor_off(in_project):
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Первый", "Второй"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "at", "01", "1"])
    assert cli.main(["work", "drop", "01", "1", "--word", "не делаем"]) == 0
    text = _text(in_project)
    assert "\nat:" not in text
    assert "курсор снят: его пункт снят" in text


def test_drop_of_the_last_undone_item_closes_the_review_gate(in_project, capsys):
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Сделанный", "Ненужный"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: taken" in _text(in_project)
    capsys.readouterr()
    assert cli.main(["work", "drop", "01", "2", "--word", "этого не надо"]) == 0
    assert "→ review" in capsys.readouterr().out
    assert "status: review" in _text(in_project)


def test_drop_refuses_a_closed_work(in_project, capsys):
    cli.main(["work", "add", "работа"])
    cli.main(["work", "checklist", "01", "Пункт"])
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "drop", "01", "1", "--word", "снимай"]) == 1
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
    _seed()
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
    cli.main(["work", "checklist", "01", "x", "второй"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "status: taken" in _text(in_project)


def test_check_double_and_missing_index_fail(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert cli.main(["work", "check", "01", "1", "--proof", "p"]) == 1
    assert "уже чекнут" in capsys.readouterr().err
    assert cli.main(["work", "check", "01", "9", "--proof", "p"]) == 1
    assert "нет пункта 9" in capsys.readouterr().err


def test_uncheck_falls_back_from_review_to_taken(in_project):
    cli.main(["work", "add", "x"])
    _seed()
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


def test_reopen_gives_a_taken_work_back_to_its_taker(in_project):
    """Кандидат 168: taken-by никуда не делся — «open» соврал бы, что работа ничья."""
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "Шаг", "Хвост"])
    cli.main(["work", "take", "01", "--by", "04-pult"])
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "reopen", "01"]) == 0
    text = _text(in_project)
    assert "status: taken" in text
    assert "taken-by: 04-pult" in text
    assert "- [x] Шаг" in text
    assert "открыта заново → taken (04-pult)" in text


def test_reopen_of_a_finished_work_lands_on_the_review_gate(in_project, capsys):
    """Всё чекнуто и человек передумал закрывать — работе место на приёмке,
    а не в очереди: делать в ней нечего, ждёт она руку."""
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "take", "01", "--by", "04-pult"])
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    cli.main(["work", "close", "01", "--word", "ок"])
    capsys.readouterr()
    assert cli.main(["work", "reopen", "01", "--word", "погоди"]) == 0
    assert "status: review" in capsys.readouterr().out
    text = _text(in_project)
    assert "status: review" in text
    assert "taken-by: 04-pult" in text
    assert "открыта заново по слову: «погоди» → taken (04-pult)" in text
    assert "все пункты чекнуты → review" in text


def test_reopen_of_a_work_nobody_took_goes_back_to_the_queue(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "close", "01", "--word", "ок"])
    assert cli.main(["work", "reopen", "01"]) == 0
    text = _text(in_project)
    assert "status: open" in text
    assert "taken-by:" not in text


# --- приёмка: у пункта два факта — «сделано» и «принято» ----------------------

def _hand_accepts(root, index, key="01"):
    """Рука с доски: попунктная приёмка — строка журнала, чеклист не трогает."""
    f = work._find(root, key) / "work.md"
    text = f.read_text(encoding="utf-8").rstrip("\n")
    f.write_text("{0}\n- 2026-07-30 14:02 — пункт {1} принят рукой\n".format(
        text, index), encoding="utf-8")


def _two_items(root):
    """Работа с двумя согласованными пунктами, взятая в работу."""
    cli.main(["work", "add", "вылить выплаты"])
    cli.main(["work", "checklist", "01", "поднять прод", "прогнать смоук"])
    cli.main(["work", "take", "01"])


def test_close_accepts_everything_checked_by_the_same_word(in_project, capsys):
    _two_items(in_project)
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    capsys.readouterr()
    assert cli.main(["work", "close", "01", "--word", "принято, закрывай"]) == 0
    assert "принято сделанное — 1 пункт(ов)" in capsys.readouterr().out
    text = _text(in_project)
    assert work.ACCEPT_ALL_LINE in text
    # порядок в журнале: сперва приёмка сделанного, следом само закрытие
    assert text.index(work.ACCEPT_ALL_LINE) < text.index("закрыта по слову")
    # чеклист не переписан — приёмка живёт строкой журнала
    assert "- [x] поднять прод\n" in text
    assert "- [ ] прогнать смоук\n" in text


def test_close_with_nothing_checked_accepts_nothing(in_project, capsys):
    cli.main(["work", "add", "x"])
    capsys.readouterr()
    assert cli.main(["work", "close", "01", "--word", "не надо, закрывай"]) == 0
    out = capsys.readouterr().out
    text = _text(in_project)
    assert work.ACCEPT_ALL_LINE not in text  # принимать нечего — строки нет
    assert "принято сделанное" not in out
    assert "закрыта по слову человека: «не надо, закрывай»" in text


def test_show_marks_an_item_accepted_by_hand(in_project, capsys):
    _two_items(in_project)
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    _hand_accepts(in_project, 1)  # жест доски, у агента такого верба нет
    capsys.readouterr()
    assert cli.main(["work", "show", "01"]) == 0
    out = capsys.readouterr().out
    assert "- [x] поднять прод ✓✓" in out  # сделано И принято
    assert "- [ ] прогнать смоук\n" in out
    assert "✓✓ — пункт сделан И принят" in out  # метка объяснена
    # файл не тронут: ✓✓ — только показ
    assert "✓✓" not in _text(in_project)


def test_show_marks_checked_items_of_a_closed_work(in_project, capsys):
    _two_items(in_project)
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    cli.main(["work", "close", "01", "--word", "остального не надо"])
    capsys.readouterr()
    cli.main(["work", "show", "01"])
    out = capsys.readouterr().out
    assert "- [x] поднять прод ✓✓" in out
    assert "- [ ] прогнать смоук\n" in out  # несделанное принимать нечем
    assert work.accepted_items(_text(in_project)) == [1]


def test_reopen_keeps_the_journal_but_the_mass_word_stops_speaking(in_project,
                                                                  capsys):
    _two_items(in_project)
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    _hand_accepts(in_project, 1)
    cli.main(["work", "close", "01", "--word", "закрывай"])
    cli.main(["work", "reopen", "01", "--word", "вернём, есть хвост"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "check", "01", "2", "--proof", "коммит def"])
    text = _text(in_project)
    assert work.ACCEPT_ALL_LINE in text  # история не трётся
    capsys.readouterr()
    cli.main(["work", "show", "01"])
    out = capsys.readouterr().out
    # пункт 1 принят рукой поимённо — метка живёт; пункт 2 сделан после
    # закрытия, и старое слово за него не говорит
    assert "- [x] поднять прод ✓✓" in out
    assert "- [x] прогнать смоук\n" in out
    assert work.accepted_items(text) == [1]


def test_unchecked_item_wears_no_acceptance_mark(in_project, capsys):
    _two_items(in_project)
    cli.main(["work", "check", "01", "1", "--proof", "коммит abc"])
    _hand_accepts(in_project, 1)
    cli.main(["work", "uncheck", "01", "1", "--reason", "не вылилось"])
    capsys.readouterr()
    cli.main(["work", "show", "01"])
    out = capsys.readouterr().out
    assert "✓✓" not in out  # два факта показываются вместе или никак
    assert "- [ ] поднять прод\n" in out


# --- title (имя работы — подпись человека) ------------------------------------

def test_title_rewrites_h1_by_the_human_word(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])
    capsys.readouterr()
    rc = cli.main(["work", "title", "01", "вылить выплаты и рефералку",
                   "--word", "назови точнее"])
    assert rc == 0
    assert "переименована: «вылить выплаты и рефералку»" in capsys.readouterr().out
    text = _text(in_project)
    assert "# вылить выплаты и рефералку\n" in text
    assert "# вылить выплаты\n" not in text
    assert "переименована словом: «назови точнее» (было: вылить выплаты)" in text
    assert "status: open" in text  # имя — не жест статуса
    # слаг папки не трогаем: это адрес, который уже держат доска и журнал
    assert work._find(in_project, "01").name == "01-vylit-vyplaty"


def test_title_refuses_without_word_and_keeps_the_file(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])
    before = _text(in_project)
    assert cli.main(["work", "title", "01", "новое имя", "--word", "  "]) == 1
    assert "имя работы меняет человек" in capsys.readouterr().err
    assert _text(in_project) == before  # отказ файл не трогает


def test_title_refuses_empty_and_the_same_name(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])
    assert cli.main(["work", "title", "01", "   ", "--word", "давай"]) == 1
    assert "пустой заголовок" in capsys.readouterr().err
    assert cli.main(["work", "title", "01", "вылить", "выплаты",
                     "--word", "давай"]) == 1
    assert "уже так и называется" in capsys.readouterr().err


def test_title_warns_on_a_long_one_but_writes_it(in_project, capsys):
    cli.main(["work", "add", "x"])
    long = "вылить выплаты, рефералку, а заодно разобрать хвосты по кабинету " \
           "и дожать смоук на проде"
    capsys.readouterr()
    assert cli.main(["work", "title", "01", long, "--word", "так и запиши"]) == 0
    out = capsys.readouterr().out
    assert "заголовок длинный ({0} символов)".format(len(long)) in out
    assert "# " + long + "\n" in _text(in_project)  # предупреждение, не отказ


def test_title_renames_a_closed_work_and_says_so(in_project, capsys):
    cli.main(["work", "add", "вылить выплаты"])
    cli.main(["work", "close", "01", "--word", "закрывай"])
    capsys.readouterr()
    assert cli.main(["work", "title", "01", "выплаты (первый заход)",
                     "--word", "переименуй, будет второй"]) == 0
    assert "закрыта — переименована задним числом" in capsys.readouterr().out
    text = _text(in_project)
    assert "# выплаты (первый заход)\n" in text
    assert "status: done" in text  # переименование закрытую не открывает


def test_title_clips_the_old_name_in_the_journal(in_project):
    old = "очень длинное имя работы, которое человек однажды написал в поле " \
          "формы на доске"
    cli.main(["work", "add", old])
    assert cli.main(["work", "title", "01", "короче", "--word", "ок"]) == 0
    text = _text(in_project)
    assert "(было: {0}…)".format(old[:59].rstrip()) in text
    assert old not in text.split("## журнал")[1]


def test_list_shows_the_new_name(in_project, capsys):
    cli.main(["work", "add", "старое имя"])
    cli.main(["work", "title", "01", "новое имя", "--word", "переименуй"])
    capsys.readouterr()
    cli.main(["work", "list"])
    out = capsys.readouterr().out
    assert "новое имя" in out
    assert "старое имя" not in out


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


def test_list_marks_proposed_items_apart_from_agreed(in_project, capsys):
    cli.main(["work", "add", "x"])
    _seed()
    cli.main(["work", "propose", "01", "предложение"])
    capsys.readouterr()
    cli.main(["work", "list"])
    assert "0/1+1?" in capsys.readouterr().out


def test_show_prints_raw_passport_with_plan_and_proposals(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "plan", "01", "как думаю делать"])
    cli.main(["work", "propose", "01", "предложение"])
    capsys.readouterr()
    rc = cli.main(["work", "show", "01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "kind: work" in out
    assert "## план" in out
    assert out.index("## план") < out.index("## чеклист")
    assert "- [?] предложение" in out


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
    f = work.works_dir(other) / "01-chuzhaya-rabota" / "work.md"
    assert f.is_file()
    # фикс ходит по тому же адресу, что и остальные вербы
    assert cli.main(["work", "fix", "01", "Накидка", "--word", "и вот это",
                     "--project", "neighbour"]) == 0
    assert "## фиксы" in f.read_text(encoding="utf-8")


# --- курсор работы (фикс 15 работы 25) ----------------------------------------

def test_at_marks_the_cursor_and_journals(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "x", "второй"])
    cli.main(["work", "take", "01"])
    rc = cli.main(["work", "at", "01", "2"])
    assert rc == 0
    assert "курсор на пункте 2 «второй»" in capsys.readouterr().out
    text = _text(in_project)
    assert "at: 2" in text
    assert "— курсор на пункте 2 «второй»" in text


def test_at_moves_and_clears(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "x", "второй"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "at", "01", "1"])
    cli.main(["work", "at", "01", "2"])
    text = _text(in_project)
    assert "\nat: 2" in text and "\nat: 1" not in text  # курсор один, он переезжает
    assert cli.main(["work", "at", "01", "--clear"]) == 0
    assert "курсор снят" in capsys.readouterr().out
    text = _text(in_project)
    assert "\nat:" not in text          # «taken-at:» не в счёт — курсор с начала строки
    assert "— курсор снят" in text


def test_at_refuses_missing_item_and_empty_call(in_project, capsys):
    cli.main(["work", "add", "x"])
    cli.main(["work", "take", "01"])
    assert cli.main(["work", "at", "01", "9"]) == 1
    assert "нет пункта 9" in capsys.readouterr().err
    assert cli.main(["work", "at", "01"]) == 1
    assert "номер пункта или --clear" in capsys.readouterr().err
    assert cli.main(["work", "at", "01", "--clear"]) == 1
    assert "курсор и так не стоит" in capsys.readouterr().err


def test_check_clears_the_cursor_of_its_own_item_only(in_project):
    cli.main(["work", "add", "x"])
    cli.main(["work", "checklist", "01", "x", "второй"])
    cli.main(["work", "take", "01"])
    cli.main(["work", "at", "01", "2"])
    # чек ЧУЖОГО пункта курсор не трогает: воркеров бывает несколько
    cli.main(["work", "check", "01", "1", "--proof", "p"])
    assert "\nat: 2" in _text(in_project)
    # чек СВОЕГО — снимает: держать «я тут» на сделанном значит врать
    cli.main(["work", "check", "01", "2", "--proof", "p"])
    assert "\nat:" not in _text(in_project)

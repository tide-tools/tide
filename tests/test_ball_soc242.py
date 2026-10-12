"""SOC-242: правило мяча и правила ночи 12.10 вшиты в tide.

«Жду» в курсоре без открытого вопроса — нарушение: Stop-хук offload-nudge блокирует
ход и требует выставить вопрос во «Вопросы» или взять работу. Законные формы
(мяч у пользователя: вопрос TG N · мяч у меня: задача + срок · отдых по слову
пользователя) — молчат.
"""

from __future__ import annotations

import io
import json

import pytest

from tide import ball, cli, escalate_rules, fields, offload
from tide.arc import stream

from tests.test_plan_main import fill_main


@pytest.fixture
def session(tmp_project):
    fill_main(stream.new_thread(tmp_project, "night", goal="держать правила ночи"))
    entry = stream.new_session(tmp_project, "night", "smena")
    fields.set_field(entry / "arc.md", "claude-session", "sid-ball")
    return entry


# --- чистый разбор курсора ------------------------------------------------------

@pytest.mark.parametrize("cursor", [
    "жду ответа пользователя",
    "Жду решения по SOC-240",
    "ждём менеджера",
    "студия ждёт выката",
    "ожидаю ответ Гриши по паузе",
    "мяч у пользователя",                         # без номера вопроса — не мяч
    "мяч у пользователя: жду его ответа",
    "мяч у меня: жду отчёт помощника",            # нет ни задачи, ни срока
    "мяч у меня: SOC-240, жду отчёт помощника",   # нет срока
])
def test_wait_without_open_question_is_a_violation(cursor):
    assert ball.violation(cursor) is not None


@pytest.mark.parametrize("cursor", [
    "мяч у пользователя: вопрос TG 4582",
    "Мяч у пользователя — вопрос тг 4610, жду только выкат",
    "мяч у меня: SOC-240, жду отчёт помощника к 03:30",
    "мяч у меня: работа 12, жду письмо board · 18 к 07:30",
    "отдых по слову пользователя",
    "отдых по слову Гриши до 09:00, жду утра",
    "чиню Stop-хук, который ловит «жду» без вопроса",   # «жду» в кавычках — цитата
    "собираю сид хендоффа",
    "",
])
def test_legit_cursors_are_silent(cursor):
    assert ball.violation(cursor) is None


def test_mine_with_eta_field_counts_as_deadline():
    assert ball.violation("мяч у меня: SOC-240, жду отчёт помощника") is not None
    assert ball.violation("мяч у меня: SOC-240, жду отчёт помощника",
                          eta="2026-10-12T07:30:00+03:00") is None


def test_cursor_of_reads_the_section(tmp_project, session):
    offload.offload(tmp_project, "night/smena", cursor="жду ответа")
    assert ball.cursor_of(session / "arc.md") == "жду ответа"


# --- Stop-хук offload-nudge ------------------------------------------------------

def _stop(monkeypatch, capsys, tmp_project, **extra):
    monkeypatch.chdir(tmp_project)
    payload = {"session_id": "sid-ball"}
    payload.update(extra)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert offload.cmd_offload_nudge(None) == 0
    return capsys.readouterr()


def test_stop_hook_blocks_wait_without_question(tmp_project, session, monkeypatch, capsys):
    offload.offload(tmp_project, "night/smena", cursor="жду ответа пользователя по паузе")
    out = _stop(monkeypatch, capsys, tmp_project).out
    verdict = json.loads(out)
    assert verdict["decision"] == "block"
    assert "правило мяча" in verdict["reason"]
    assert "tg-alert" in verdict["reason"] and "мяч у пользователя: вопрос TG" in verdict["reason"]
    assert "night/smena" in verdict["reason"]  # готовая команда offload с рабочей ссылкой


@pytest.mark.parametrize("cursor", [
    "мяч у пользователя: вопрос TG 4582",
    "мяч у меня: SOC-240, жду отчёт помощника к 03:30",
    "отдых по слову пользователя",
])
def test_stop_hook_silent_on_legit_ball(tmp_project, session, monkeypatch, capsys, cursor):
    offload.offload(tmp_project, "night/smena", cursor=cursor)
    assert _stop(monkeypatch, capsys, tmp_project).out.strip() == ""


def test_stop_hook_anti_loop(tmp_project, session, monkeypatch, capsys):
    offload.offload(tmp_project, "night/smena", cursor="жду ответа")
    assert _stop(monkeypatch, capsys, tmp_project, stop_hook_active=True).out.strip() == ""


def test_stop_hook_stands_down_when_dissolved(tmp_project, session, monkeypatch, capsys):
    offload.offload(tmp_project, "night/smena", cursor="жду ответа")
    fields.set_field(session / "arc.md", "dissolved", "2026-10-12T05:00:00")
    assert _stop(monkeypatch, capsys, tmp_project).out.strip() == ""


# --- tide offload предупреждает сразу ---------------------------------------------

def test_cli_offload_warns_on_wait_cursor(tmp_project, session, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    assert cli.main(["offload", "night/smena", "--cursor", "жду ответа менеджера"]) == 0
    assert "правило мяча" in capsys.readouterr().err
    assert cli.main(["offload", "night/smena", "--cursor",
                     "мяч у пользователя: вопрос TG 4582"]) == 0
    assert "правило мяча" not in capsys.readouterr().err


# --- правила в файле и строка мяча в сидах ------------------------------------------

def test_night_rules_live_in_thread_rules_file():
    text = escalate_rules.rules_file().read_text(encoding="utf-8")
    r = escalate_rules.read_rules()
    assert "tg-alert" in r["questions"] and "⭐" in r["questions"]
    assert "не дублировать" in r["questions"] and "переспрос" in r["questions"]
    assert "мяч у пользователя: вопрос TG N" in r["ball"] and "offload-nudge" in r["ball"]
    assert "лимитов квоты по проектам нет" in r["limit"]
    assert "☐" in r["user_tasks"] and "🖐" in r["user_tasks"]
    assert "≤5 мин" in r["reply"]
    assert "правилу мяча" in text  # цикл ссылается на правило мяча (SOC-234)


def test_ball_line_rides_in_launch_seed():
    from tide.launcher import seed
    s = seed.build_seed(project_name="proj", arc_ref="worker-1", thread_name="worker",
                        arc_text="x")
    assert ball.BALL_LINE in s


def test_seed_cursor_block_flags_a_wait_cursor():
    from tide.launcher import seed_draft
    bad = seed_draft.block_cursor(["жду ответа пользователя"], [])
    assert any(ball.SEED_FLAG in ln for ln in bad)
    ok = seed_draft.block_cursor(["мяч у пользователя: вопрос TG 4582"], [])
    assert not any(ball.SEED_FLAG in ln for ln in ok)


def test_ask_gate_points_to_the_board_not_the_manager_window():
    from tide.hooks import ask_gate
    assert "tg-alert" in ask_gate.DENY_TEMPLATE and "⭐" in ask_gate.DENY_TEMPLATE
    assert "SendMessage" not in ask_gate.DENY_TEMPLATE

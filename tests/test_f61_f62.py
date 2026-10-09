"""SOC-101 acceptance-06 §9: F61 и F62.

F62 — подсказка «назови хвосты меткой «Хвосты:»» (и предупреждение «Ожидание
Гриши:», и «Карта:») видит метку в любом месте пульса ПЕРЕДАЧА после начала,
« · », «. », «; » — тем же разборщиком, что сборщик сида.
F61 — ``tide thread layer <нить> --main "…" [--replace]`` пишет «главное» в
plan.md нити (его читает сборщик через plan_main.live_lines).

Всё на временных каталогах.
"""

from __future__ import annotations

import pytest

from tide import cli, handoff_contract, offload
from tide.arc import plan_main, stream, thread_layers as tl

from tests.test_plan_main import fill_main


@pytest.fixture
def session(tmp_project):
    fill_main(stream.new_thread(tmp_project, "hygiene", goal="keep the seam clean"))
    return stream.new_session(tmp_project, "hygiene", "otliv")


# --- F62 ----------------------------------------------------------------------

LONG = ("ПЕРЕДАЧА. Где мы: шаг 3, воркер v2.4 на приёмке, доска перерисована {sep}"
        "Ждёт Гришу: SOC-112 на приёмке {sep}В полёте: воркер v2.4 {sep}"
        "{label} нет {sep}Решения: вкладка РЕШЕНИЯ {sep}Опыт: role-gate режет curl")


@pytest.mark.parametrize("sep", ["· ", ". ", "; "])
def test_tails_label_mid_pulse_no_hint(tmp_project, session, monkeypatch, capsys, sep):
    monkeypatch.chdir(tmp_project)
    note = LONG.format(sep=sep, label="Хвосты:") + " · Ожидание Гриши: «к пятнице» (tg 1)"
    assert cli.main(["offload", "otliv", note]) == 0
    err = capsys.readouterr().err
    assert "меткой «Хвосты:»" not in err
    assert "нет метки «Ожидание Гриши:»" not in err


def test_acceptance_06_pulse_shape(tmp_project, session, monkeypatch, capsys):
    """Пульс P:55 — все метки через « · », хвосты в середине: подсказок нет."""
    monkeypatch.chdir(tmp_project)
    note = ("ПЕРЕДАЧА. Где мы: шаг 3 · Ожидание Гриши: «доска с телефона» (tg 3365) · "
            "Ждёт Гришу: SOC-112 · В полёте: воркер · Хвосты: verify красный · "
            "Решения: вкладка · Опыт: role-gate · Карта: /abs/plan.md — план")
    assert cli.main(["offload", "otliv", note]) == 0
    err = capsys.readouterr().err
    assert "Хвосты:" not in err and "Ожидание Гриши:" not in err and "Карта:" not in err


@pytest.mark.parametrize("sep", ["· ", ". ", "; "])
def test_map_label_mid_pulse(sep):
    note = "ПЕРЕДАЧА. Где мы: шаг 3 {0}Карта: /a — x; /b — y".format(sep)
    assert not handoff_contract.pass_note_without_map(note)


def test_missing_labels_still_hinted(tmp_project, session, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    assert cli.main(["offload", "otliv", "ПЕРЕДАЧА. Где мы: шаг 3 · хвосты ещё есть"]) == 0
    err = capsys.readouterr().err
    assert "меткой «Хвосты:»" in err
    assert "нет метки «Ожидание Гриши:»" in err
    assert "нет метки «Карта:»" in err


def test_label_keys_include_empty_label():
    assert {"tails", "expectation"} <= offload._pass_label_keys(
        "ПЕРЕДАЧА · Ожидание: к пятнице · Хвосты:")


# --- F61 ----------------------------------------------------------------------

def _thread(tmp_project, name="seam"):
    return stream.new_thread(tmp_project, name, goal="держать шов")


def test_main_fills_scaffold_and_seed_reads_it(tmp_project, monkeypatch, capsys):
    th = _thread(tmp_project)
    monkeypatch.chdir(tmp_project)
    assert plan_main.live_lines(th) == []
    rc = cli.main(["thread", "layer", "seam", "--main",
                   "SOC-101 · сдавать management-18 (ao send) · не дублировать с tide-stack-24"])
    assert rc == 0
    assert plan_main.live_lines(th) == [
        "- SOC-101", "- сдавать management-18 (ao send)", "- не дублировать с tide-stack-24"]
    assert plan_main.problem(th) is None
    text = (th / "plan.md").read_text(encoding="utf-8")
    assert "<SOC-N" not in text and text.startswith("# план нити")
    assert "«главное» дописано" in capsys.readouterr().out


def test_main_appends_keeps_live_and_replace(tmp_project, monkeypatch):
    th = _thread(tmp_project)
    monkeypatch.chdir(tmp_project)
    plan = th / "plan.md"
    plan.write_text("# план нити seam · v1\n\n## главное — читать первым\n- SOC-1\n"
                    "\n## шаги\n- [ ] шаг 1\n", encoding="utf-8")
    assert cli.main(["thread", "layer", "seam", "--main", "кому: main",
                     "--main", "SOC-1"]) == 0
    assert plan_main.live_lines(th) == ["- SOC-1", "- кому: main"]
    assert "## шаги\n- [ ] шаг 1" in plan.read_text(encoding="utf-8")
    assert cli.main(["thread", "layer", "seam", "--replace", "--main", "SOC-2 · x · y"]) == 0
    assert plan_main.live_lines(th) == ["- SOC-2", "- x", "- y"]
    assert "## шаги\n- [ ] шаг 1" in plan.read_text(encoding="utf-8")


def test_main_creates_plan_and_section(tmp_project):
    th = _thread(tmp_project)
    (th / "plan.md").unlink()
    assert tl.write_main(th, ["SOC-3"]) == ["SOC-3"]
    assert plan_main.live_lines(th) == ["- SOC-3"]
    (th / "plan.md").write_text("# план\n\n## шаги\n- [ ] a\n", encoding="utf-8")
    tl.write_main(th, ["SOC-4"])
    text = (th / "plan.md").read_text(encoding="utf-8")
    assert text.index(plan_main.HEADING) < text.index("## шаги")
    assert plan_main.live_lines(th) == ["- SOC-4"]


def test_exit_warning_names_main_when_empty(tmp_project):
    th = _thread(tmp_project)
    warn = tl.exit_warning(th)
    assert warn and "--main" in warn
    fill_main(th)
    warn = tl.exit_warning(th)
    assert warn and "--main" not in warn


def test_layer_without_any_flag_errors(tmp_project, monkeypatch):
    _thread(tmp_project)
    monkeypatch.chdir(tmp_project)
    with pytest.raises(tl.LayersError) as e:
        tl.cmd_layer(type("A", (), {"target": "seam"})())
    assert "--main" in str(e.value)


def test_split_main_keeps_session_names_f70():
    """SOC-143 F70: «роль · NN» в --main — имя сессии, не разделитель."""
    assert tl.split_main(["сдавать: менеджеру и main · 05 tide-stack-31; вид — через "
                          "designer · 09 · не дублировать: X"]) == [
        "сдавать: менеджеру и main · 05 tide-stack-31; вид — через designer · 09",
        "не дублировать: X"]
    assert tl.split_main(["сдавать (main · 05, tide-stack-31) · проверка"]) == [
        "сдавать (main · 05, tide-stack-31)", "проверка"]
    assert tl.split_main(["шаг 1 · шаг 2 · шаг 1"]) == ["шаг 1", "шаг 2"]

"""Решение Гриши № 24: «## главное — читать первым» в plan.md нити — каркас,
старт-гейт первого пульса, блок сида."""

from __future__ import annotations

from tide import cli, handoff_contract as hc
from tide.arc import curate, plan_main, stream, templates
from tide.launcher import handoff_audit as ha, seed_draft

FILLED = ("- SOC-101 https://linear.app/x/issue/SOC-101\n"
          "- сдавать: management — main · NN через ao send\n"
          "- не дублировать: board, topics\n")


def fill_main(tdir):
    """Для чужих тестов: «главное» нити заполнено — старт-гейт пропускает пульс."""
    _fill(tdir)


def _fill(tdir, body=FILLED):
    (tdir / "plan.md").write_text(
        "# план\n\n{0}\n{1}\n## шаги\n\n- [ ] 1. a | b | c\n".format(plan_main.HEADING, body),
        encoding="utf-8")


def test_template_seeds_the_main_section_with_three_hints():
    text = templates.thread_plan_md("x")
    assert plan_main.HEADING in text
    assert plan_main.section(text) == ["- " + h for h in plan_main.HINTS]
    assert "plan-main: required" in templates.thread_goal_md("x")


def test_new_thread_is_born_with_plan_and_mark(tmp_project):
    t = stream.new_thread(tmp_project, "fresh", goal="цель")
    assert plan_main.read(t) is not None and plan_main.live_lines(t) == []
    assert plan_main.required(t) and plan_main.is_scaffold(t)
    assert "одни подсказки" in plan_main.problem(t)
    curate.drop_thread(t)  # нетронутый каркас нить не наполняет — сбрасывается


def _offload(tmp_project, monkeypatch, ref, *words):
    monkeypatch.chdir(tmp_project)
    return cli.main(["offload", ref, *words])


def test_start_gate_blocks_first_pulse_until_main_is_filled(tmp_project, monkeypatch,
                                                             capsys):
    t = stream.new_thread(tmp_project, "gated", goal="цель")
    s = stream.new_session(tmp_project, "gated", "one")
    assert _offload(tmp_project, monkeypatch, "gated/one", "старт") == 1
    err = capsys.readouterr().err
    assert "старт-гейт не закрыт" in err and "SOC-N" in err and "одни подсказки" in err
    assert "старт" not in (s / "arc.md").read_text(encoding="utf-8").split("## context")[1]

    _fill(t, "- SOC-101\n- <кому сдавать>\n")
    assert _offload(tmp_project, monkeypatch, "gated/one", "старт") == 1
    assert "1 из 3" in capsys.readouterr().err

    _fill(t)
    assert _offload(tmp_project, monkeypatch, "gated/one", "старт") == 0
    assert "главное" not in capsys.readouterr().err


def test_pulsed_thread_only_warns_once_per_session(tmp_project, monkeypatch, capsys):
    t = stream.new_thread(tmp_project, "warm", goal="цель")
    _fill(t)
    stream.new_session(tmp_project, "warm", "one")
    assert _offload(tmp_project, monkeypatch, "warm/one", "работа") == 0
    _fill(t, "")   # «главное» стёрли после старта — блока нет, есть предупреждение
    stream.new_session(tmp_project, "warm", "two")
    capsys.readouterr()
    assert _offload(tmp_project, monkeypatch, "warm/two", "первый") == 0
    assert "⚠" in capsys.readouterr().err
    assert _offload(tmp_project, monkeypatch, "warm/two", "второй") == 0
    assert "⚠" not in capsys.readouterr().err


def test_old_thread_without_mark_is_warned_not_blocked(tmp_project, monkeypatch, capsys):
    from tide import fields

    t = stream.new_thread(tmp_project, "old", goal="цель")
    goal = stream.passport_path(t)
    goal.write_text(goal.read_text(encoding="utf-8").replace("plan-main: required\n", ""),
                    encoding="utf-8")
    (t / "plan.md").unlink()
    assert not plan_main.required(t) and fields.read_field(goal, "kind") == "thread"
    stream.new_session(tmp_project, "old", "one")
    assert _offload(tmp_project, monkeypatch, "old/one", "пульс") == 0
    err = capsys.readouterr().err
    assert "⚠" in err and "нет plan.md" in err


def test_seed_carries_main_first(tmp_project):
    t = stream.new_thread(tmp_project, "seeded", goal="цель")
    _fill(t)
    s = stream.new_session(tmp_project, "seeded", "one")
    bodies = seed_draft.draft_bodies(tmp_project, s)
    assert bodies[seed_draft.MAIN][0].startswith("- SOC-101")
    out = seed_draft.render_draft(bodies)
    assert out.index("## главное") < out.index("## ожидание Гриши")
    assert hc.NAMES[0] == "главное"
    assert hc.find_body(seed_draft.sections("## главное — читать первым\n- x\n"),
                        "main") == ["- x"]


def test_seed_main_falls_back_to_input_seed(tmp_project):
    t = stream.new_thread(tmp_project, "inh", goal="цель")
    s = stream.new_session(tmp_project, "inh", "one")
    (s / "input").mkdir(exist_ok=True)
    (s / "input" / seed_draft.INPUT_SEED_NAME).write_text(
        "## главное\n\n- SOC-7\n- сдавать: X\n- не дублировать: Y\n", encoding="utf-8")
    assert seed_draft.draft_bodies(tmp_project, s)[seed_draft.MAIN] == [
        "- SOC-7", "- сдавать: X", "- не дублировать: Y"]
    assert t.is_dir()


def test_audit_measures_main(tmp_project):
    assert ("main", "главное: plan.md нити") == ha.AUDIT_SECTIONS[0]

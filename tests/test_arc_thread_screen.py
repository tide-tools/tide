"""unit — arc.thread_screen: `tide thread`, the one screen + the one check (work 64)."""

from __future__ import annotations

import pytest

from tide import offload
from tide.arc import decision, stream, thread_screen as ts


PLAN = """# план нити

## шаги

- [x] 1. первый шаг | что делается | результат
- [~] 2. растворённый шаг | снят | —
- [>] 3. текущий шаг | что делается | результат
- [ ] 4. будущий шаг | что делается | результат
"""


def _thread(proj, slug="release", goal="отдать стек своим"):
    stream.new_thread(proj, slug, goal=goal)
    return slug


def _with_plan(proj, slug="release"):
    tdir = ts.resolve_thread(proj, slug)
    (tdir / "plan.md").write_text(PLAN, encoding="utf-8")
    return tdir


# --- resolution --------------------------------------------------------------

def test_a_lone_thread_needs_no_naming(tmp_project):
    """A person at a terminal with one thread should not have to name it."""
    _thread(tmp_project)
    assert ts.resolve_thread(tmp_project).name.endswith("-@release")


def test_two_threads_without_a_ref_ask_which(tmp_project):
    _thread(tmp_project, "release")
    _thread(tmp_project, "cleanup")
    with pytest.raises(ts.ThreadError, match="which one"):
        ts.resolve_thread(tmp_project)


def test_unknown_ref_lists_what_is_open(tmp_project):
    _thread(tmp_project)
    with pytest.raises(ts.ThreadError, match="no open thread matching"):
        ts.resolve_thread(tmp_project, "ghost")


# --- the screen --------------------------------------------------------------

def test_screen_leads_with_goal_and_the_current_step(tmp_project):
    _thread(tmp_project)
    _with_plan(tmp_project)
    out = ts.render(tmp_project, "release")
    assert out.startswith("release — thread ")
    assert "goal: отдать стек своим" in out
    assert "▸ 3  текущий шаг" in out          # the current step is marked
    assert "первый шаг" not in out            # closed steps don't take up the screen


def test_screen_counts_a_retired_step_without_listing_it_as_open(tmp_project):
    """A step the parser can't see is how a plan and its screen start disagreeing."""
    _thread(tmp_project)
    _with_plan(tmp_project)
    out = ts.render(tmp_project, "release")
    assert "plan · 4 steps, 1 closed, 1 retired" in out
    assert "растворённый шаг" not in out


def test_screen_leads_with_the_promises_nobody_carried_out(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "доска едет в коробке", thread_ref=nit, dslug="box")
    decision.add_decision(tmp_project, "коробка = git clone", thread_ref=nit, dslug="clone")
    decision.add_decision(tmp_project, "приёмка — живой проход", thread_ref=nit,
                          dslug="crit", rule=True)
    decision.mark_done(tmp_project, "clone", "приёмка в докере 24/24", thread_ref=nit)
    out = ts.render(tmp_project, nit)
    assert "3 in force (1 carried out, 1 not, 1 standing rules)" in out
    assert "in force, NOT carried out:" in out
    assert "доска едет в коробке" in out          # the unkept promise is on the screen…
    assert "коробка = git clone" not in out       # …the kept one is just a count
    assert "standing rules — 1" in out


def test_screen_says_who_carries_each_unkept_promise(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "с исполнителем", thread_ref=nit, dslug="owned")
    decision.add_decision(tmp_project, "без исполнителя", thread_ref=nit, dslug="orphan")
    decision.accept(tmp_project, "owned", work="65", thread_ref=nit)
    out = ts.render(tmp_project, nit)
    assert "→ work 65" in out
    assert "→ no work" in out


def test_screen_names_the_real_files_in_a_session_workspace(tmp_project):
    """A path SHAPE ('arcs/<session>/workspace/') is not an answer to 'where'."""
    nit = _thread(tmp_project)
    entry = stream.new_session(tmp_project, nit, "priemka")
    ws = entry / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / "karta-niti.md").write_text("карта", encoding="utf-8")
    out = ts.render(tmp_project, nit)
    assert "where to look" in out
    assert "01-priemka/workspace/ — karta-niti.md" in out


def test_screen_skips_a_session_whose_workspace_is_empty(tmp_project):
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "priemka")
    assert "/workspace/ —" not in ts.render(tmp_project, nit)


def test_screen_reads_the_summary_a_session_wrote_along_the_way(tmp_project):
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", summary="карта нити и три отчёта дня")
    out = ts.render(tmp_project, nit)
    assert "карта нити и три отчёта дня" in out


def test_screen_never_prints_the_template_placeholder_as_a_summary(tmp_project):
    """The placeholder spans lines; half of it read as a summary for every arc."""
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", note="что-то сделал")
    out = ts.render(tmp_project, nit)
    assert "written on handoff" not in out
    assert "no summary" in out


def test_screen_survives_a_thread_with_nothing_in_it(tmp_project):
    nit = _thread(tmp_project, goal="")
    out = ts.render(tmp_project, nit)
    assert "plan · none" in out and "decisions · none" in out and "sessions · none" in out


def test_screen_ends_with_the_check_verdict(tmp_project):
    nit = _thread(tmp_project)
    assert ts.render(tmp_project, nit).rstrip().endswith("check · clean")


# --- the one check -----------------------------------------------------------

def test_check_is_green_on_a_thread_that_keeps_the_rules(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d", rule=True)
    stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", note="работал", summary="что тут лежит")
    assert ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)]) == []
    assert "clean" in ts.render_check(tmp_project, nit)


def test_check_finds_a_live_promise_with_nobody_carrying_it(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "обещание", thread_ref=nit, dslug="d")
    found = ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
    assert [x.rule for x in found] == ["decision-owner"]
    assert "no work is carrying it" in found[0].what
    assert found[0].fix.startswith("tide decision accept 01 --work")


def test_check_is_silent_once_a_work_owns_the_promise(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "обещание", thread_ref=nit, dslug="d")
    decision.accept(tmp_project, "d", work="65", thread_ref=nit)
    assert ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)]) == []


def test_check_never_cries_about_a_standing_rule(tmp_project):
    """A rule has no work and never will — firing on it fires forever, so it gets muted."""
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "приёмка — живой проход", thread_ref=nit,
                          dslug="crit", rule=True)
    assert ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)]) == []


def test_check_finds_a_carried_out_decision_that_shows_nothing(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d")
    f = decision.decisions_file(ts.resolve_thread(tmp_project, nit))
    f.write_text(f.read_text(encoding="utf-8").replace("done: —", "done: 2026-09-01"),
                 encoding="utf-8")
    found = ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
    assert len(found) == 1 and "nothing shows it" in found[0].what


def test_check_finds_a_live_arc_that_says_nothing_about_itself(tmp_project):
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", note="полдня работы")
    found = ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
    assert [x.rule for x in found] == ["arc-summary"]
    assert found[0].fix == 'tide offload priemka --summary "…"'


def test_check_spares_an_arc_that_has_not_done_anything_yet(tmp_project):
    """A session that never wrote a line owes no summary — that is how a check stays quiet."""
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "fresh")
    assert ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)]) == []


def test_check_spares_closed_sessions(tmp_project):
    nit = _thread(tmp_project)
    entry = stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", note="работал")
    entry.rename(entry.parent / "__{0}__".format(entry.name))
    assert ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)]) == []


def test_check_finds_a_decision_filed_under_the_wrong_thread(tmp_project):
    nit = _thread(tmp_project, "release")
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d", rule=True)
    f = decision.decisions_file(ts.resolve_thread(tmp_project, nit))
    f.write_text(f.read_text(encoding="utf-8").replace("thread: release", "thread: cleanup"),
                 encoding="utf-8")
    found = ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
    assert [x.rule for x in found] == ["filing"]
    assert "says thread: cleanup" in found[0].what


def test_check_ignores_a_cross_project_address_it_cannot_verify(tmp_project):
    """Crying because a neighbour's roster is unreachable is a false alarm about us."""
    from tide.arc import work

    nit = _thread(tmp_project)
    card = work.new_work(tmp_project, "чужая работа") / "work.md"
    card.write_text(
        card.read_text(encoding="utf-8").replace(
            "status: open", "thread: neighbour/40-@payouts\nstatus: open", 1),
        encoding="utf-8")
    assert [f for f in ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
            if f.rule == "filing"] == []


def test_check_finds_a_live_work_with_no_thread_at_all(tmp_project):
    from tide.arc import work

    nit = _thread(tmp_project)
    work.new_work(tmp_project, "бездомная работа")
    found = [f for f in ts.check(tmp_project, [ts.resolve_thread(tmp_project, nit)])
             if f.rule == "filing"]
    assert len(found) == 1 and "no thread address" in found[0].what


def test_check_output_is_capped_so_it_can_never_become_a_wall(tmp_project):
    nit = _thread(tmp_project)
    for i in range(ts.CAP + 5):
        decision.add_decision(tmp_project, "решение {0}".format(i), thread_ref=nit)
    out = ts.render_check(tmp_project, nit)
    assert "decision-owner ({0})".format(ts.CAP + 5) in out
    assert "(+5 more of the same)" in out
    assert out.count("fix: tide decision accept") == ts.CAP


def test_check_all_sweeps_every_open_thread(tmp_project):
    a = _thread(tmp_project, "release")
    b = _thread(tmp_project, "cleanup")
    for nit in (a, b):
        stream.new_session(tmp_project, nit, "s-" + nit)
        offload.offload(tmp_project, "s-" + nit, note="работал")
    out = ts.render_check(tmp_project, every=True)
    assert "all open threads" in out and "arc-summary (2)" in out


def test_screen_reads_a_plan_that_never_wrote_the_shagi_header(tmp_project):
    """Half the live plans put steps straight under the title; 'no plan' was a lie."""
    _thread(tmp_project)
    tdir = ts.resolve_thread(tmp_project, "release")
    (tdir / "plan.md").write_text(
        "# план нити\n\nfinal: двор чистый\n\n"
        "- [>] 1. опись двора | обойти расписания | список хвостов\n"
        "- [ ] 2. шум замолкает | заглушить старое | телефон не звонит\n\n"
        "## границы\n- ничего не удаляем без слова человека\n",
        encoding="utf-8")
    out = ts.render(tmp_project, "release")
    assert "plan · 2 steps, 0 closed" in out
    assert "▸ 1  опись двора" in out
    assert "ничего не удаляем" not in out     # prose is not mistaken for a step


def test_one_finding_reads_as_singular(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d")
    assert "check · 1 finding —" in ts.render(tmp_project, nit)


# --- the cold-session questions the screen has to answer outright ------------

def test_screen_marks_the_current_step_written_in_prose_not_as_a_marker(tmp_project):
    """Two live conventions; reading only [>] said 'no step' where a human wrote one."""
    _thread(tmp_project)
    tdir = ts.resolve_thread(tmp_project, "release")
    (tdir / "plan.md").write_text(
        "# план\n\nfinal: свой человек ставит стек одной командой\n\n## шаги\n\n"
        "- [x] 1. первый | … | …\n"
        "- [ ] 9. отдать двоим | … | …\n"
        "- [ ] 10. система рассказывает о себе | … | …\n\n"
        "## текущий шаг — 9 (отдать двоим) и 10 (система рассказывает о себе)\n",
        encoding="utf-8")
    out = ts.render(tmp_project, "release")
    assert "▸ 9  отдать двоим" in out
    assert "▸ 10 система рассказывает о себе" in out
    assert "final: свой человек ставит стек одной командой" in out
    assert "2 left" in out


def test_screen_says_when_nobody_named_a_current_step(tmp_project):
    _thread(tmp_project)
    tdir = ts.resolve_thread(tmp_project, "release")
    (tdir / "plan.md").write_text("# план\n\n- [ ] 1. шаг | … | …\n", encoding="utf-8")
    out = ts.render(tmp_project, "release")
    assert "nobody has said where we are" in out


def test_screen_shows_what_is_happening_now_in_the_sessions_own_words(tmp_project):
    nit = _thread(tmp_project)
    stream.new_session(tmp_project, nit, "priemka")
    offload.offload(tmp_project, "priemka", cursor="снимаю повторный замер бенчмарка")
    out = ts.render(tmp_project, nit)
    assert "now · снимаю повторный замер бенчмарка" in out
    assert "its own cursor" in out


def test_screen_points_at_the_settled_arguments_without_printing_them_all(tmp_project):
    nit = _thread(tmp_project)
    for i in range(3):
        decision.add_decision(tmp_project, "решение {0}".format(i), thread_ref=nit,
                              closes="спор {0} — не перерешивать".format(i))
    out = ts.render(tmp_project, nit)
    assert "already rejected — 3 settled arguments" in out
    assert "tide decision list --closes" in out
    assert "спор 0" not in out           # the door, not thirty-one bodies


def test_closes_listing_prints_what_each_decision_settled(tmp_project):
    nit = _thread(tmp_project)
    decision.add_decision(tmp_project, "решение", thread_ref=nit, dslug="d",
                          closes="спор «а или б» — не перерешивать")
    decision.add_decision(tmp_project, "без closes", thread_ref=nit, dslug="bare")
    out = decision.render_list(tmp_project, thread_ref=nit, closes=True)
    assert "closes: спор «а или б» — не перерешивать" in out
    assert "bare" not in out             # a record that settled nothing says nothing

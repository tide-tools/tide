"""черновик сида (работа 15 / кандидат 153) — tide handoffs draft.

Харнес компонует ЧЕРНОВИК по контракту семи блоков из накопленного на диске;
уставший агент курирует. Проверяем: маппинг источник→блок, порядок и имена
блоков, правило пустоты (``—``), и что draft НИЧЕГО не вешает в очередь.
"""

from __future__ import annotations

import pytest

from tide import offload
from tide.arc import candidate, decision, stream
from tide.launcher import seed_draft


@pytest.fixture
def session(tmp_project):
    """Нить с целью + одна сессия внутри — минимальная сцена для черновика."""
    stream.new_thread(tmp_project, "sloy", goal="единый слой: одно место, где видно живьём")
    return stream.new_session(tmp_project, "sloy", "shov", goal="довести шов хендоффа")


# --- разбор секций (чистое) --------------------------------------------------

def test_sections_splits_on_headers_and_keeps_bodies():
    secs = seed_draft.sections("# h\n\n## cursor — resume here\nстою тут\n\n## next\nдальше\n")
    assert secs["cursor — resume here"] == ["стою тут", ""]
    assert secs["next"] == ["дальше"]


def test_section_body_matches_by_prefix():
    secs = seed_draft.sections("## cursor — resume here\nстою тут\n")
    assert seed_draft.section_body(secs, "cursor") == ["стою тут"]
    assert seed_draft.section_body(secs, "context") == []


def test_live_lines_drops_template_placeholders():
    body = ["<where this session left off>", "", "настоящая строка"]
    assert seed_draft.live_lines(body) == ["настоящая строка"]


# --- блоки (чистое) ----------------------------------------------------------

def test_block_final_carries_thread_goal_and_this_step():
    out = seed_draft.block_final("единый слой", "довести шов")
    assert out == ["нить: единый слой", "этот шаг: довести шов"]


def test_block_final_collapses_the_inherited_duplicate():
    # сессия рождается с целью нити — печатать её дважды нечестно
    assert seed_draft.block_final("единый слой", "единый слой") == ["нить: единый слой"]


def test_block_final_empty_without_goals():
    assert seed_draft.block_final("", "") == []


def test_block_cursor_puts_cursor_first_then_pulse_newest_on_top():
    out = seed_draft.block_cursor(
        ["строю доску"],
        ["- 01:00 — первое", "- 02:00 — второе", "- 03:00 — третье"],
        tail=2,
    )
    assert out == ["строю доску", "- 03:00 — третье", "- 02:00 — второе"]


def test_block_cursor_bullets_a_bare_pulse_line():
    out = seed_draft.block_cursor([], ["голая строка"])
    assert out == ["- голая строка"]


def test_work_line_says_number_name_status_and_what_it_waits_for():
    rec = {"num": "28", "title": "Кандидаты на доске", "status": "review",
           "done": 2, "total": 2, "at": 0}
    assert seed_draft.work_line(rec) == (
        "- работа 28 Кандидаты на доске — review, ждёт закрытия словом")


def test_work_line_of_a_taken_work_carries_progress_and_the_cursor():
    rec = {"num": "18", "title": "Переезд к Виталию", "status": "taken",
           "done": 1, "total": 14, "at": 3}
    assert seed_draft.work_line(rec) == (
        "- работа 18 Переезд к Виталию — taken 1/14, курсор на пункте 3")


def test_work_line_invents_no_place_without_a_cursor():
    rec = {"num": "07", "title": "Шов", "status": "taken", "done": 0,
           "total": 4, "at": 0}
    assert seed_draft.work_line(rec) == "- работа 07 Шов — taken 0/4"


def test_work_line_of_an_open_work_waits_for_a_taker():
    rec = {"num": "09", "title": "Пульт", "status": "open", "done": 0,
           "total": 2, "at": 0}
    assert seed_draft.work_line(rec) == (
        "- работа 09 Пульт — open 0/2, ждёт исполнителя")


def test_work_line_does_not_call_an_open_work_free_while_someone_is_on_it():
    """Кандидат 168: open + чужой taken-by — паспорт врёт, записка не подхватывает."""
    rec = {"num": "31", "title": "Вопрос уходит карточкой", "status": "open",
           "taken_by": "04-pult", "done": 1, "total": 1, "at": 0}
    assert seed_draft.work_line(rec) == (
        "- работа 31 Вопрос уходит карточкой — open 1/1, числится за 04-pult, "
        "статус не сходится — смотри карточку")


def test_work_line_does_not_call_an_open_work_free_while_items_are_done():
    rec = {"num": "31", "title": "Вопрос уходит карточкой", "status": "open",
           "taken_by": "", "done": 1, "total": 2, "at": 0}
    assert seed_draft.work_line(rec) == (
        "- работа 31 Вопрос уходит карточкой — open 1/2, исполнителя нет, "
        "а пункты сделаны — статус не сходится, смотри карточку")


def test_work_line_still_waits_for_a_taker_when_the_card_agrees():
    """Чистый случай не трогаем: никого нет и ничего не сделано."""
    rec = {"num": "09", "title": "Пульт", "status": "open", "taken_by": "",
           "done": 0, "total": 2, "at": 0}
    assert seed_draft.work_line(rec) == (
        "- работа 09 Пульт — open 0/2, ждёт исполнителя")


def test_block_cursor_puts_works_under_the_cursor_newest_first():
    works = [
        {"num": "9", "title": "Младшая", "status": "open", "total": 0},
        {"num": "28", "title": "Кандидаты", "status": "review", "total": 2},
    ]
    out = seed_draft.block_cursor(["стою тут"], ["- 01:00 — было"], works=works)
    assert out == [
        "стою тут",
        "- работа 28 Кандидаты — review, ждёт закрытия словом",
        "- работа 9 Младшая — open, ждёт исполнителя",
        "- 01:00 — было",
    ]


def test_block_next_splits_the_middot_chain():
    assert seed_draft.block_next(["принять работу · чекнуть шаг 2 · собрать сид"]) == [
        "- принять работу", "- чекнуть шаг 2", "- собрать сид",
    ]


def test_block_decisions_puts_the_unkept_promises_first():
    recs = [
        {"num": "01", "what": "выполнено", "status": "accepted",
         "done": "2026-09-01", "slug": "a"},
        {"num": "02", "what": "живое", "status": "accepted", "slug": "b"},
        {"num": "03", "what": "история", "status": "superseded", "slug": "c"},
        {"num": "04", "what": "снято", "status": "dropped", "slug": "d"},
    ]
    assert seed_draft.block_decisions(recs) == [
        "- 02 — живое (not done)",
        "- 01 — выполнено (done)",
    ]


def test_block_decisions_reads_the_pre_two_axis_spellings():
    """Old logs say open/settled — the seed must still group them right."""
    recs = [
        {"num": "01", "what": "осевшее", "status": "settled", "status_raw": "settled",
         "slug": "a"},
        {"num": "02", "what": "живое", "status": "open", "status_raw": "open",
         "slug": "b"},
    ]
    assert seed_draft.block_decisions(recs) == [
        "- 02 — живое (not done)",
        "- 01 — осевшее (done)",
    ]


def test_block_experience_ranges_a_contiguous_run():
    (line, scaffold) = seed_draft.block_experience(["146", "147", "148"])
    assert line == "- кандидаты сессии: 146–148"
    assert scaffold.startswith("- —")


def test_block_experience_lists_a_gapped_run():
    line, _scaffold = seed_draft.block_experience(["146", "149"])
    assert line == "- кандидаты сессии: 146, 149"


def test_block_experience_without_candidates_is_scaffold_only():
    assert len(seed_draft.block_experience([])) == 1


def test_block_entry_map_is_a_scaffold_the_agent_fills():
    (only,) = seed_draft.block_entry_map()
    assert only.startswith("- —") and "пишет агент" in only


# --- сборка (чистое) ---------------------------------------------------------

def test_render_draft_keeps_contract_order_and_all_seven_blocks():
    out = seed_draft.render_draft({"финал": ["нить: X"]})
    heads = [ln[3:] for ln in out.splitlines() if ln.startswith("## ")]
    assert heads == list(seed_draft.BLOCKS)


def test_render_draft_fills_an_empty_block_with_a_visible_hole():
    out = seed_draft.render_draft({"финал": ["нить: X"]})
    body = out.partition("## курсор")[2]
    assert body.strip().startswith(seed_draft.EMPTY)


# --- сборка с диска ----------------------------------------------------------

def _loaded(tmp_project, session_dir):
    """Наполнить сцену: пульс, решения, кандидат, входной сид с окружением."""
    offload.write_pulse(
        session_dir,
        note="разложили маппинг источник→блок",
        cursor="воркер строит tide handoffs draft",
        next_steps="принять работу 16 · чекнуть шаг 2",
    )
    decision.add_decision(tmp_project, "контракт сида: семь блоков", thread_ref="sloy")
    decision.add_decision(tmp_project, "воркеры видны только через работу", thread_ref="sloy")
    decision.settle(tmp_project, "01", thread_ref="sloy")
    candidate.new_candidate(tmp_project, "harness-draft", from_arc=session_dir.name)
    seed_in = session_dir / "input" / seed_draft.INPUT_SEED_NAME
    seed_in.parent.mkdir(parents=True, exist_ok=True)
    seed_in.write_text(
        "# handoff\n\n## окружение\n\n- доска = launchd-служба на :8452\n",
        encoding="utf-8",
    )


def test_build_draft_maps_every_source_to_its_block(tmp_project, session):
    _loaded(tmp_project, session)
    out = seed_draft.build_draft(tmp_project, session)

    assert "нить: единый слой: одно место, где видно живьём" in out
    assert "этот шаг: довести шов хендоффа" in out
    assert "воркер строит tide handoffs draft" in out          # курсор
    assert "разложили маппинг источник→блок" in out            # хвост пульса
    assert "- принять работу 16" in out and "- чекнуть шаг 2" in out
    assert "- 02 — воркеры видны только через работу (not done)" in out
    assert "- 01 — контракт сида: семь блоков (done)" in out
    assert "- кандидаты сессии: 01" in out
    assert "- доска = launchd-служба на :8452" in out          # окружение как есть
    # решения: open идёт ПЕРЕД settled
    assert out.index("- 02 — воркеры") < out.index("- 01 — контракт")


def test_build_draft_carries_the_threads_live_works(tmp_project, session):
    """Записка несёт состояние работ сама — принимающему не идти за ним на доску."""
    from tide.arc import work

    thread = session.parents[1].name
    work.new_work(tmp_project, "Кандидаты на доске")
    work.set_checklist(tmp_project, "01", ["разложить"])
    work.set_thread(tmp_project, "01", thread)
    work.take(tmp_project, "01", by="воркер", thread=thread)
    work.check(tmp_project, "01", 1, proof="пруф")
    work.new_work(tmp_project, "Работа чужой нити")
    work.set_thread(tmp_project, "02", "99-@chuzhaya")
    offload.write_pulse(session, cursor="стою тут")

    cursor = seed_draft.build_draft(tmp_project, session) \
        .partition("## курсор")[2].partition("\n## ")[0]
    assert "- работа 01 Кандидаты на доске — review, ждёт закрытия словом" in cursor
    assert "Работа чужой нити" not in cursor


def test_build_draft_on_a_bare_session_is_holes_not_invention(tmp_project):
    stream.new_thread(tmp_project, "pusto")
    bare = stream.new_session(tmp_project, "pusto", "start")
    out = seed_draft.build_draft(tmp_project, bare)
    # ни одной выдуманной строки: блоки без источника — с «—»
    for block in ("финал", "курсор", "дальше", "решения", "окружение"):
        body = out.partition("## {0}".format(block))[2].partition("\n## ")[0]
        assert body.strip() == seed_draft.EMPTY


def test_build_draft_ignores_a_foreign_sessions_candidates(tmp_project, session):
    candidate.new_candidate(tmp_project, "chuzhoy", from_arc="99-other")
    out = seed_draft.build_draft(tmp_project, session)
    assert "кандидаты сессии" not in out


def test_build_draft_without_input_seed_leaves_environment_empty(tmp_project, session):
    offload.write_pulse(session, cursor="стою тут")
    out = seed_draft.build_draft(tmp_project, session)
    assert out.partition("## окружение")[2].strip() == seed_draft.EMPTY


# --- резолв сессии + запись --------------------------------------------------

def test_draft_writes_into_the_sessions_workspace(tmp_project, session):
    _loaded(tmp_project, session)
    path, text = seed_draft.draft(tmp_project, "shov")
    assert path == session / "workspace" / seed_draft.DRAFT_FILENAME
    assert path.read_text(encoding="utf-8") == text
    assert text.startswith("# черновик сида — {0}".format(session.name))


def test_draft_defaults_to_the_current_session_by_sid(tmp_project, session, monkeypatch):
    from tide import fields

    fields.set_field(session / "arc.md", "claude-session", "sid-42")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-42")
    path, _text = seed_draft.draft(tmp_project)
    assert path.parent.parent == session


def test_draft_without_sid_or_ref_asks_for_the_arc(tmp_project, session):
    with pytest.raises(seed_draft.SeedDraftError, match="CLAUDE_CODE_SESSION_ID"):
        seed_draft.draft(tmp_project)


def test_draft_unknown_session_lists_the_open_ones(tmp_project, session):
    with pytest.raises(seed_draft.SeedDraftError, match="no open session"):
        seed_draft.draft(tmp_project, "nesuschestvuyet")


# --- CLI ---------------------------------------------------------------------

def test_cli_handoffs_draft_prints_and_writes(tmp_project, session, monkeypatch, capsys):
    from tide import cli

    _loaded(tmp_project, session)
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["handoffs", "draft", "shov"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "seed draft →" in out
    assert "## карта входа" in out                      # черновик целиком в stdout
    assert (session / "workspace" / seed_draft.DRAFT_FILENAME).is_file()


def test_draft_hangs_no_offer_and_moves_no_status(tmp_project, session, monkeypatch, tmp_path):
    from tests.conftest import build_tide_skeleton
    from tide import fields, handoff_queue

    home = tmp_path / "control-home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    monkeypatch.setenv("TIDE_HOME", str(home))
    before = (session / "arc.md").read_text(encoding="utf-8")

    seed_draft.draft(tmp_project, "shov")

    assert handoff_queue.list_offers(home) == []
    assert (session / "arc.md").read_text(encoding="utf-8") == before
    assert (fields.read_field(session / "arc.md", "status") or "") == "active"

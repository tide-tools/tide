"""19-tide-plan-board unit — the focus board: pure ops + board.json on a goal-arc."""

from __future__ import annotations

import json

import pytest

from tide.arc import plan, stream


# --- pure focus ops --------------------------------------------------------

def test_add_card_appends_with_deterministic_id():
    board = plan._empty_board()
    board, card = plan.add_card(board, "first")
    assert card["id"] == "c1"
    assert board["focus"]["cards"][0]["text"] == "first"


def test_add_card_is_immutable():
    board = plan._empty_board()
    plan.add_card(board, "x")
    assert board["focus"]["cards"] == []  # original untouched


def test_add_card_empty_text_raises():
    with pytest.raises(plan.PlanError):
        plan.add_card(plan._empty_board(), "   ")


def test_focus_gate_refuses_the_eighth_card():
    board = plan._empty_board()
    for i in range(plan.FOCUS_LIMIT):
        board, _ = plan.add_card(board, "c{0}".format(i))
    with pytest.raises(plan.PlanError):
        plan.add_card(board, "overflow")


def test_drop_moves_card_to_backlog_not_deleted():
    board = plan._empty_board()
    board, _ = plan.add_card(board, "keep")
    board, dropped = plan.add_card(board, "drop me")
    board = plan.drop_card(board, dropped["id"])
    ids = [c["id"] for c in board["focus"]["cards"]]
    backlog = [c["id"] for c in board["focus"]["backlog"]]
    assert dropped["id"] not in ids
    assert dropped["id"] in backlog


def test_drop_unknown_id_raises():
    with pytest.raises(plan.PlanError):
        plan.drop_card(plan._empty_board(), "cZ")


def test_dropped_id_is_never_reused():
    board = plan._empty_board()
    board, c1 = plan.add_card(board, "a")
    board = plan.drop_card(board, c1["id"])
    board, c2 = plan.add_card(board, "b")
    assert c2["id"] != c1["id"]  # backlog id excluded from the next-id scan


# --- path (≤3) -------------------------------------------------------------

def test_step_appends_to_path():
    board = plan.add_step(plan._empty_board(), "do the thing")
    assert board["plan"][0]["text"] == "do the thing"


def test_path_gate_refuses_the_fourth_step():
    board = plan._empty_board()
    for i in range(plan.PLAN_LIMIT):
        board = plan.add_step(board, "step {0}".format(i))
    with pytest.raises(plan.PlanError):
        plan.add_step(board, "one too many")


# --- distill (compression axis) --------------------------------------------

def test_distill_records_history_and_reseeds_focus():
    board = plan._empty_board()
    board, chosen = plan.add_card(board, "big messy idea")
    board, _ = plan.add_card(board, "sibling")
    board = plan.distill(board, chosen["id"], "the formula")
    # levels + history recorded
    assert board["compression"]["levels"] == ["the formula"]
    assert board["compression"]["history"][0]["formula"] == "the formula"
    assert board["compression"]["history"][0]["choice"]["id"] == chosen["id"]
    # focus reseeded to a single card = the formula, tagged with the level
    assert len(board["focus"]["cards"]) == 1
    seed = board["focus"]["cards"][0]
    assert seed["text"] == "the formula"
    assert seed["level"] == 1


def test_distill_unknown_id_raises():
    with pytest.raises(plan.PlanError):
        plan.distill(plan._empty_board(), "cZ", "formula")


# --- sync-codes ------------------------------------------------------------

def test_sync_plain_text_adds_a_card():
    board, note = plan.apply_sync(plan._empty_board(), "a fresh thought")
    assert board["focus"]["cards"][0]["text"] == "a fresh thought"
    assert "added" in note


def test_sync_drop_token_moves_to_backlog():
    board = plan._empty_board()
    board, card = plan.add_card(board, "x")
    board, note = plan.apply_sync(board, "DROP:{0}".format(card["id"]))
    assert board["focus"]["backlog"][0]["id"] == card["id"]
    assert "dropped" in note


def test_sync_bare_distill_is_refused():
    with pytest.raises(plan.PlanError):
        plan.apply_sync(plan._empty_board(), "DISTILL")


# --- disk: board.json on a resolved goal-arc -------------------------------

def test_board_file_hangs_in_the_arc_workspace(tmp_project):
    stream.new_arc(tmp_project, "myarc")
    path = plan.board_file(tmp_project, "myarc")
    assert path.name == "board.json"
    assert path.parent.name == "workspace"
    assert "01-myarc" in str(path)


def test_load_board_defaults_when_absent(tmp_project):
    stream.new_arc(tmp_project, "myarc")
    board = plan.load_board(tmp_project, "myarc")
    assert board["focus"]["cards"] == []
    assert board["focus"]["limit"] == plan.FOCUS_LIMIT


def test_save_then_load_round_trips(tmp_project):
    stream.new_arc(tmp_project, "myarc")
    board, _ = plan.add_card(plan.load_board(tmp_project, "myarc"), "persist me")
    plan.save_board(tmp_project, "myarc", board)
    again = plan.load_board(tmp_project, "myarc")
    assert again["focus"]["cards"][0]["text"] == "persist me"
    # file is real, valid JSON
    raw = json.loads(plan.board_file(tmp_project, "myarc").read_text(encoding="utf-8"))
    assert raw["focus"]["cards"][0]["text"] == "persist me"


def test_unresolved_goal_raises(tmp_project):
    with pytest.raises(plan.PlanError):
        plan.board_file(tmp_project, "ghost-arc")


# --- HTML projection -------------------------------------------------------

def test_build_html_injects_board_and_meta_no_placeholders_left():
    board = plan._empty_board()
    board, _ = plan.add_card(board, "a real focus card")
    html = plan.build_html(board, {"name": "wake", "thread": "myarc", "goal": "ship it"})
    assert "__BOARD_JSON__" not in html and "__META_JSON__" not in html
    assert "a real focus card" in html  # data is inlined (works under the artifact CSP)
    assert "ship it" in html


def test_build_html_standalone_wraps_a_document():
    html = plan.build_html(plan._empty_board(), {"thread": "x"}, standalone=True)
    assert html.lstrip().startswith("<!doctype html>")
    assert "viewport" in html  # mobile-first


def test_build_html_fragment_has_no_document_wrapper():
    frag = plan.build_html(plan._empty_board(), {"thread": "x"}, standalone=False)
    assert "<!doctype" not in frag.lower()
    assert "<style>" in frag  # the Artifact skeleton supplies head/body


# --- threads mirror the real tide stream -----------------------------------

def test_refresh_threads_mirrors_open_threads_with_weight(tmp_project):
    # two real tide threads; one carries an open session (heavier pull)
    stream.new_thread(tmp_project, "alpha")
    stream.new_thread(tmp_project, "beta")
    stream.new_session(tmp_project, "alpha", "work")  # alpha now has 1 open session
    board = plan.refresh_threads(tmp_project, plan._empty_board())
    by_slug = {t["slug"]: t for t in board["threads"]}
    assert set(by_slug) == {"alpha", "beta"}
    assert by_slug["alpha"]["weight"] >= 1
    assert by_slug["beta"]["weight"] == 1  # floored at 1 even with no sessions


def test_refresh_threads_replaces_seed_values(tmp_project):
    stream.new_thread(tmp_project, "real-thread")
    seeded = plan._empty_board()
    seeded["threads"] = [{"name": "FAKE", "weight": 99}]
    board = plan.refresh_threads(tmp_project, seeded)
    names = [t["name"] for t in board["threads"]]
    assert "FAKE" not in names
    assert any(t["slug"] == "real-thread" for t in board["threads"])


def test_refresh_threads_empty_when_no_threads(tmp_project):
    stream.new_arc(tmp_project, "just-an-arc")  # a plain arc is NOT a thread
    board = plan.refresh_threads(tmp_project, plan._empty_board())
    assert board["threads"] == []


# --- deep-plan surface -----------------------------------------------------

def _seed_plan_data(tmp_project, slug, **extra):
    arc = stream.new_arc(tmp_project, slug)
    ws = arc / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    data = {"topic": "t", "title": "Build the thing",
            "steps": [{"title": "первый шаг", "status": "now", "depth": ["под-пункт"]}],
            "decisions": [{"q": "развилка?", "opts": "а · б"}], "next": "следующий ход"}
    data.update(extra)
    (ws / plan.PLAN_DATA_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return arc


def test_build_surface_injects_plan_data(tmp_project):
    _seed_plan_data(tmp_project, "make-plan")
    html = plan.build_surface(tmp_project, "make-plan")
    assert "__PLAN_JSON__" not in html
    assert "Build the thing" in html
    assert "первый шаг" in html and "следующий ход" in html


def test_build_surface_standalone_vs_fragment(tmp_project):
    _seed_plan_data(tmp_project, "make-plan")
    doc = plan.build_surface(tmp_project, "make-plan", standalone=True)
    frag = plan.build_surface(tmp_project, "make-plan", standalone=False)
    assert doc.lstrip().startswith("<!doctype html>")
    assert "<!doctype" not in frag.lower()


def test_build_surface_missing_data_raises(tmp_project):
    stream.new_arc(tmp_project, "bare-arc")  # no plan-data.json
    with pytest.raises(plan.PlanError):
        plan.build_surface(tmp_project, "bare-arc")


# --- show channel (agent puts things on the board) -------------------------

def test_show_on_board_appends_item(tmp_project):
    stream.new_arc(tmp_project, "topic")
    item = plan.show_on_board(tmp_project, "topic", "смотри сюда", body="подробнее")
    assert item["title"] == "смотри сюда"
    data = json.loads(plan.plan_data_file(tmp_project, "topic").read_text(encoding="utf-8"))
    assert data["shows"][0]["title"] == "смотри сюда"
    assert data["shows"][0]["body"] == "подробнее"


def test_show_on_board_creates_data_when_absent(tmp_project):
    stream.new_arc(tmp_project, "topic")  # no plan-data.json yet
    plan.show_on_board(tmp_project, "topic", "first show")
    assert plan.plan_data_file(tmp_project, "topic").is_file()


def test_show_then_surface_renders_the_show(tmp_project):
    stream.new_arc(tmp_project, "topic")
    plan.show_on_board(tmp_project, "topic", "канал жив", kind="схема")
    html = plan.build_surface(tmp_project, "topic")
    assert "канал жив" in html and "схема" in html


def test_show_empty_title_raises(tmp_project):
    stream.new_arc(tmp_project, "topic")
    with pytest.raises(plan.PlanError):
        plan.show_on_board(tmp_project, "topic", "   ")

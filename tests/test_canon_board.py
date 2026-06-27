"""U8 unit — the canon status board (group by 5-state · needs-you slice · open asks)."""

from __future__ import annotations

from tide import strictness
from tide.arc import stream
from tide.canon import board
from tide.contract import ask as ask_mod
from tide.contract import lifecycle


def test_no_contracts(tmp_project):
    assert board.render_board(tmp_project) == "CANON\n  (no contracts)"


def test_render_board_groups_by_state_full_snapshot(tmp_project):
    # strict dial (fixture default) → a draft awaits the human's signature
    stream.new_arc(tmp_project, "a1")
    lifecycle.new(tmp_project, "a1")                       # draft

    stream.new_arc(tmp_project, "a2")
    lifecycle.new(tmp_project, "a2")
    lifecycle.sign(tmp_project, "a2")                      # running

    stream.new_arc(tmp_project, "a3")
    lifecycle.new(tmp_project, "a3")
    lifecycle.sign(tmp_project, "a3")
    lifecycle.report(tmp_project, "a3")
    lifecycle.proof(tmp_project, "a3")                     # output

    ask_mod.ask(tmp_project, "a1", "which-db")

    expected = (
        "CANON\n"
        "\n"
        "NEEDS YOU\n"
        "  01-a1  [draft]  a1\n"
        "  03-a3  [output]  a3\n"
        "\n"
        "draft (1)\n"
        "  01-a1  a1\n"
        "\n"
        "running (1)\n"
        "  02-a2  a2\n"
        "\n"
        "output (1)\n"
        "  03-a3  a3\n"
        "\n"
        "OPEN ASKS\n"
        "  01-a1 · 01-which-db"
    )
    assert board.render_board(tmp_project) == expected


def test_loose_dial_draft_is_not_needs_you(tmp_project):
    strictness.set_strictness(tmp_project, "loose")
    stream.new_arc(tmp_project, "x")
    lifecycle.new(tmp_project, "x")  # draft, but loose → orchestrator signs, not a gate
    out = board.render_board(tmp_project)
    assert "NEEDS YOU" not in out
    assert "draft (1)" in out


def test_strict_dial_draft_is_needs_you(tmp_project):
    stream.new_arc(tmp_project, "x")
    lifecycle.new(tmp_project, "x")
    assert "NEEDS YOU" in board.render_board(tmp_project)


def test_answered_ask_not_listed(tmp_project):
    stream.new_arc(tmp_project, "a1")
    lifecycle.new(tmp_project, "a1")
    ask_mod.ask(tmp_project, "a1", "which-db")
    ask_mod.answer(tmp_project, "a1", "which-db", answer="postgres")
    out = board.render_board(tmp_project)
    assert "OPEN ASKS" not in out

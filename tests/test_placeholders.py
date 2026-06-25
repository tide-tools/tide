"""F5 unit — tide.placeholders: detect leftover scaffold placeholders in docs."""

from __future__ import annotations

from tide import placeholders
from tide.arc import templates
from tide.contract import model


# --- find_in_text ----------------------------------------------------------

def test_clean_doc_has_no_placeholders():
    text = "# 01-alpha\n\ngoal: ship the fix\nstatus: done\n\n## input\nthe leak report\n"
    assert placeholders.find_in_text(text) == []


def test_angle_bracket_span_is_flagged():
    found = placeholders.find_in_text("goal: <one line — what this arc closes>\n")
    assert found == ["<one line — what this arc closes>"]


def test_supersedes_hint_is_flagged_once():
    # The hint line carries its own `<…>` span, but it is reported once as the
    # stripped hint — not double-counted with its angle span.
    text = "status: active\n# supersedes: <slug of the arc this one replaces>\n"
    found = placeholders.find_in_text(text)
    assert found == ["# supersedes: <slug of the arc this one replaces>"]


def test_multiple_placeholders_in_document_order():
    text = (
        "goal: <a>\n"
        "criteria: <b>\n"
        "## where we are\n"
        "<c>\n"
    )
    assert placeholders.find_in_text(text) == ["<a>", "<b>", "<c>"]


def test_real_supersedes_field_is_not_a_placeholder():
    # A filled `supersedes: old-plan` (no `# ` comment, no angle span) is clean.
    text = "status: active\nsupersedes: old-plan\n"
    assert placeholders.find_in_text(text) == []


def test_stray_lessthan_without_close_is_not_swallowed():
    # A bare `<` that never closes on the line is not a placeholder span.
    assert placeholders.find_in_text("note: a < b in the formula\n") == []


# --- the real templates all carry placeholders -----------------------------

def test_fresh_arc_template_is_full_of_placeholders():
    found = placeholders.find_in_text(templates.arc_md("01-alpha"))
    assert len(found) >= 3
    assert any("# supersedes:" in f for f in found)


def test_fresh_goal_template_is_full_of_placeholders():
    found = placeholders.find_in_text(templates.goal_md("ship"))
    assert found  # the H1 `<goal>` + goal body + supersedes hint


def test_fresh_contract_template_is_full_of_placeholders():
    found = placeholders.find_in_text(model.contract_md("fix-leak"))
    # goal/criteria/project defaults + IS→TO-BE + where-we-are + supersedes hint
    assert len(found) >= 5


# --- find_in_file ----------------------------------------------------------

def test_find_in_file_missing_returns_empty(tmp_path):
    assert placeholders.find_in_file(tmp_path / "nope.md") == []


def test_find_in_file_reads_disk(tmp_path):
    p = tmp_path / "arc.md"
    p.write_text(templates.arc_md("01-alpha"), encoding="utf-8")
    assert placeholders.find_in_file(p)


# --- refuse_message --------------------------------------------------------

def test_refuse_message_names_doc_ref_and_lists_offenders():
    msg = placeholders.refuse_message("arc.md", "alpha", ["<a>", "<b>"])
    assert "placeholder" in msg
    assert "arc.md" in msg
    assert "'alpha'" in msg
    assert "-f" in msg
    assert "  - <a>" in msg
    assert "  - <b>" in msg

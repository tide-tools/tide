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


# --- code spans are not placeholders (candidate 109) ------------------------

def test_angle_span_inside_inline_backticks_is_ignored():
    # `<arg>` inside backticks is a documented example, not an unfilled field.
    assert placeholders.find_in_text("run `tide go <arg>` to start\n") == []


def test_angle_span_inside_fenced_block_is_ignored():
    text = (
        "## How used\n\n"
        "```\n"
        "tide arc new <slug>\n"
        "tide contract sign <slug> --signer <role>\n"
        "```\n"
    )
    assert placeholders.find_in_text(text) == []


def test_tilde_fenced_block_is_ignored():
    text = "~~~\n<placeholder> example\n~~~\n"
    assert placeholders.find_in_text(text) == []


def test_bare_angle_span_outside_code_still_flagged():
    # The whole point: a real unfilled placeholder in prose is still caught.
    assert placeholders.find_in_text("goal: <fill me in>\n") == ["<fill me in>"]


def test_mixed_line_flags_only_the_bare_span():
    # Bare `<real>` + backticked `<code>` on one line → only the bare one is a
    # placeholder; the backticked example is skipped.
    found = placeholders.find_in_text("fill <real> like `<code>` here\n")
    assert found == ["<real>"]


def test_multiple_inline_spans_on_one_line_all_masked():
    found = placeholders.find_in_text("use `<a>` and `<b>` together\n")
    assert found == []


def test_unmatched_backtick_does_not_mask_following_span():
    # A stray opening backtick with no closer must NOT swallow a later prose span.
    found = placeholders.find_in_text("a ` stray then <real>\n")
    assert found == ["<real>"]


def test_backticked_one_line_goal_form_is_clean():
    # The exact form that tripped the gate twice today (the backticked example of
    # the goal H1 placeholder) must pass — no guillemet workaround needed.
    assert placeholders.find_in_text("see `<one line — what this arc closes>`\n") == []


# --- unterminated fence must not swallow placeholders below it ---------------

def test_unterminated_backtick_fence_does_not_hide_placeholder_below():
    # A ``` opener that never closes is a broken doc, not a code block: a real
    # <placeholder> below it must STILL be flagged (completeness > masking).
    text = "## s\n```\ncode\n\n<fill me in>\n"
    assert placeholders.find_in_text(text) == ["<fill me in>"]


def test_unterminated_tilde_fence_does_not_hide_placeholder_below():
    text = "## s\n~~~\ncode\n\n<fill me in>\n"
    assert placeholders.find_in_text(text) == ["<fill me in>"]


def test_unterminated_fence_flags_placeholders_both_before_and_after():
    # `<real before>` (prose, before the broken fence) + unterminated fence +
    # `<real after>` (below it) → BOTH are flagged.
    text = "<real before>\n```\ncode\n<real after>\n"
    assert placeholders.find_in_text(text) == ["<real before>", "<real after>"]


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
